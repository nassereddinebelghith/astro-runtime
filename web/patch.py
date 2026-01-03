# ============================================================================
# MÉTHODE 1: refresh_user()
# ============================================================================
def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
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
        
        # Update session ONLY if we're in a request context
        if has_request_context():
            session['access_token'] = tokens["access_token"]
            session['refresh_token'] = tokens["refresh_token"]
            session.modified = True
        
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


# ============================================================================
# MÉTHODE 2: _is_authorized()
# ============================================================================
def _is_authorized(
    self,
    *,
    method,
    resource_type: KeycloakResource,
    user: KeycloakAuthManagerUser,
    resource_id=None,
    attributes=None,
) -> bool:
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Check if token is expired and refresh if needed
    if self._token_expired(user.access_token):
        refreshed = self.refresh_user(user=user)
        
        if not refreshed:
            logger.info("Token refresh failed in _is_authorized")
            return False
        
        # Token refreshed successfully, update user
        user = refreshed
    
    # Extract HTTP method
    method_str = method.value if hasattr(method, "value") else str(method)
    
    # Special case: GET without resource_id means LIST
    if method_str.upper() == "GET" and resource_id is None:
        method_str = "LIST"
    
    # Extract roles from token and check authorization
    roles = self._extract_roles_from_token(user.access_token)
    return self._role_allows(roles, resource_type, method_str)
