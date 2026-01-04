class OldcKeycloakAuthManager(KeycloakAuthManager):
    """
    Custom Keycloak Auth Manager with role mapping and token refresh handling
    """
    
    # ========================================================================
    # MÉTHODE 1: refresh_user() - FIX selon GitHub issue #59359
    # ========================================================================
    def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
        """
        Refresh user authentication.
        Returns None if refresh fails → triggers re-authentication.
        
        Fix based on: https://github.com/apache/airflow/issues/59359
        """
        from flask import has_request_context, session
        from keycloak.exceptions import KeycloakPostError
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            # If token still valid → nothing to do
            if not self._token_expired(user.access_token):
                return user

            # Token expired, attempt refresh
            logger.debug("Token expired, attempting refresh")
            
            client = self.get_keycloak_client()
            
            # ⭐ KEY FIX: Wrap in try/except to catch KeycloakPostError
            try:
                tokens = client.refresh_token(user.refresh_token)
            except KeycloakPostError as exc:
                # ⭐ This is the fix from issue #59359
                logger.warning(
                    "KeycloakPostError encountered during token refresh. "
                    "Suppressing the exception and returning None.",
                    exc_info=exc
                )
                # Clear session
                if has_request_context():
                    session.clear()
                
                # Return None = user not authenticated
                return None
            
            # Refresh succeeded - update user object
            user.access_token = tokens["access_token"]
            user.refresh_token = tokens.get("refresh_token", user.refresh_token)
            
            # Update session
            if has_request_context():
                session['access_token'] = tokens["access_token"]
                session['refresh_token'] = tokens.get("refresh_token", user.refresh_token)
                session.modified = True
            
            logger.info("Token refreshed successfully")
            return user
            
        except Exception as exc:
            # Any other unexpected error
            logger.exception(f"Unexpected error in refresh_user: {exc}")
            if has_request_context():
                session.clear()
            return None
    
    # ========================================================================
    # MÉTHODE 2: is_logged_in() - Force redirection quand refresh échoue
    # ========================================================================
    def is_logged_in(self) -> bool:
        """
        Check if user is logged in.
        Returns False when token refresh fails → Airflow redirects to login.
        """
        from flask import session, has_request_context
        import logging
        
        logger = logging.getLogger(__name__)
        
        # Get user from session
        user = self.get_user()
        
        if user is None:
            return False
        
        # Check if token expired
        if self._token_expired(user.access_token):
            logger.info("Token expired, attempting refresh")
            
            # Try refresh
            refreshed = self.refresh_user(user=user)
            
            if not refreshed:
                # Refresh failed - clear session
                logger.warning("Token refresh failed, clearing session")
                if has_request_context():
                    session.clear()
                
                # Return False → Airflow redirects to login
                return False
            
            # Refresh succeeded - update session with new tokens
            if has_request_context():
                session['access_token'] = refreshed.access_token
                session['refresh_token'] = refreshed.refresh_token
                session.modified = True
        
        return True
