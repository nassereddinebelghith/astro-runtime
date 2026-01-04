# ============================================================================
# MÉTHODE 1: get_current_user() - NOUVELLE MÉTHODE À AJOUTER
# ============================================================================
def get_current_user(self) -> KeycloakAuthManagerUser | None:
    """
    Override to handle token expiration at the user level.
    Called by Airflow to get the current authenticated user.
    """
    from flask import session, has_request_context
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Call parent implementation to get user
    user = super().get_current_user()
    
    if user is None:
        return None
    
    # Check if token is expired
    if self._token_expired(user.access_token):
        logger.info(f"Token expired for user {user.email} in get_current_user")
        
        # Try to refresh
        refreshed = self.refresh_user(user=user)
        
        if not refreshed:
            # Refresh failed - clear session and return None
            logger.warning(f"Token refresh failed in get_current_user, user will be logged out")
            
            if has_request_context():
                session.clear()
            
            # Return None = user not authenticated
            # Airflow will automatically redirect to login
            return None
        
        # Refresh succeeded
        return refreshed
    
    return user


# ============================================================================
# MÉTHODE 2: _is_authorized() - VERSION SIMPLIFIÉE
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
    """
    Check if user is authorized to access a resource.
    Token expiration is already handled in get_current_user().
    """
    import logging
    
    logger = logging.getLogger(__name__)
    
    # Extract HTTP method
    method_str = method.value if hasattr(method, "value") else str(method)
    
    # Special case: GET without resource_id means LIST
    if method_str.upper() == "GET" and resource_id is None:
        method_str = "LIST"
    
    # Extract roles from token and check authorization
    roles = self._extract_roles_from_token(user.access_token)
    
    # Debug logging
    logger.info(f"Authorization: user={user.email}, roles={roles}, resource={resource_type}, method={method_str}")
    
    result = self._role_allows(roles, resource_type, method_str)
    
    if not result:
        logger.warning(f"Authorization DENIED for {user.email}: {method_str} on {resource_type}")
    
    return result


# ============================================================================
# MÉTHODE 3: refresh_user() - GARDER COMME AVANT
# ============================================================================
def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
    from flask import has_request_context, session
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        if not self._token_expired(user.access_token):
            return user

        client = self.get_keycloak_client()
        tokens = client.refresh_token(user.refresh_token)

        user.refresh_token = tokens["refresh_token"]
        user.access_token = tokens["access_token"]
        
        if has_request_context():
            session['access_token'] = tokens["access_token"]
            session['refresh_token'] = tokens["refresh_token"]
            session.modified = True
        
        logger.info("Token refreshed successfully")
        return user

    except KeycloakPostError as exc:
        msg = str(exc)
        
        if getattr(exc, "response_code", None) == 401:
            if has_request_context():
                session.clear()
            logger.info(f"Received 401 from Keycloak: {msg}")
            return None
        
        if getattr(exc, "response_code", None) == 400 and "invalid_grant" in msg:
            if has_request_context():
                session.clear()
            logger.info(f"Token refresh failed (invalid_grant): {msg}")
            return None
        
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
