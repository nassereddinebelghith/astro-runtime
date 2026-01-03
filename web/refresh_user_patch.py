"""
Corrected refresh_user() implementation for Keycloak Auth Manager
Airflow 3.1.5 with Keycloak 0.4.1

This properly handles token expiration by returning None instead of raising exceptions
"""

def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
    """
    Refresh user authentication on each request.
    
    Returns:
        - User object if token is valid or successfully refreshed
        - None if token expired and refresh failed (triggers redirect to login)
    """
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
        
        # CRITICAL: Update session with new tokens to persist them
        from flask import session
        session['access_token'] = tokens["access_token"]
        session['refresh_token'] = tokens["refresh_token"]
        session.modified = True
        
        return user

    except KeycloakPostError as exc:
        msg = str(exc)
        
        # Token refresh failed (invalid_grant, token not active, etc.)
        if getattr(exc, "response_code", None) == 400 and "invalid_grant" in msg:
            # IMPORTANT: Return None instead of raising exception
            # This tells Airflow the user is no longer authenticated
            # Airflow will automatically redirect to login page
            
            # Optional: Clear the session to be clean
            from flask import session
            session.clear()
            
            # Log for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Token refresh failed (invalid_grant): {msg}. User will be redirected to login.")
            
            return None  # ← KEY CHANGE: Return None instead of raising HTTPException

        # Any other Keycloak error: log it and return None
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Keycloak error during token refresh: {msg}")
        
        # Return None to trigger re-authentication
        return None

    except Exception as exc:
        # Unexpected error: log and return None
        import logging
        logger = logging.getLogger(__name__)
        logger.exception(f"Unexpected error in refresh_user: {exc}")
        
        # Return None to trigger re-authentication
        return None
