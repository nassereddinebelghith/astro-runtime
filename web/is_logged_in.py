class OldcKeycloakAuthManager(KeycloakAuthManager):
    """
    Custom Keycloak Auth Manager with role mapping and token refresh handling
    """
    
    # ========================================================================
    # MÉTHODE 1: is_logged_in() - NOUVELLE MÉTHODE À AJOUTER
    # ========================================================================
    def is_logged_in(self) -> bool:
        """
        Check if user is logged in.
        Returns False when token refresh fails → Airflow redirects to login.
        """
        from flask import session, has_request_context
        import logging
        
        logger = logging.getLogger(__name__)
        
        # Get user from session (calls parent method)
        user = self.get_user()
        
        if user is None:
            return False
        
        # Check if token expired
        if self._token_expired(user.access_token):
            logger.info("Token expired, attempting refresh")
            
            # Try refresh
            refreshed = self.refresh_user(user=user)
            
            if not refreshed:
                # Refresh failed
                logger.warning("Token refresh failed, clearing session")
                
                if has_request_context():
                    session.clear()
                
                # Return False → Airflow redirects to login automatically
                return False
            
            # Refresh succeeded - update session
            if has_request_context():
                session['access_token'] = refreshed.access_token
                session['refresh_token'] = refreshed.refresh_token
                session.modified = True
        
        return True
    
    # ========================================================================
    # MÉTHODE 2: refresh_user() - GARDER COMME AVANT
    # ========================================================================
    def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
        """
        Refresh user authentication on each request.
        Handles token expiration and automatic refresh.
        """
        from flask import has_request_context, session
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            # If token still valid → nothing to do
            if not self._token_expired(user.access_token):
                return user

            # Token expired, attempt refresh
            client = self.get_keycloak_client()
            tokens = client.refresh_token(user.refresh_token)

            # Update user with new tokens
            user.refresh_token = tokens["refresh_token"]
            user.access_token = tokens["access_token"]
            
            logger.info("Token refreshed successfully")
            return user

        except KeycloakPostError as exc:
            msg = str(exc)
            
            # Handle 401 - token expired
            if getattr(exc, "response_code", None) == 401:
                if has_request_context():
                    session.clear()
                logger.info(f"Received 401 from Keycloak: {msg}")
                return None
            
            # Handle 400 + invalid_grant
            if getattr(exc, "response_code", None) == 400 and "invalid_grant" in msg:
                if has_request_context():
                    session.clear()
                logger.info(f"Token refresh failed (invalid_grant): {msg}")
                return None
            
            # Handle 403
            if getattr(exc, "response_code", None) == 403:
                if has_request_context():
                    session.clear()
                logger.info(f"Received 403 from Keycloak: {msg}")
                return None

            logger.error(f"Keycloak error: {msg}")
            return None

        except Exception as exc:
            logger.exception(f"Unexpected error in refresh_user: {exc}")
            return None
