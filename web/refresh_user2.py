def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
    """
    Refresh user authentication.
    Returns None if refresh fails → triggers re-authentication.
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
        
        # ⭐ KEY: Wrap in try/except to catch KeycloakPostError
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
        
        # Refresh succeeded
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
