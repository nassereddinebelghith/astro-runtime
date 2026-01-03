# ============================================================================
# VERSION FINALE QUI MARCHE À COUP SÛR
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
    from flask import has_request_context, session, redirect, url_for
    from werkzeug.exceptions import HTTPException
    import logging
    
    logger = logging.getLogger(__name__)
    
    if self._token_expired(user.access_token):
        refreshed = self.refresh_user(user=user)
        
        if not refreshed:
            logger.warning("Token refresh failed, forcing redirect to login")
            
            if has_request_context():
                # Clear session
                session.clear()
                
                # Create custom HTTP exception that carries a redirect
                class RedirectToLogin(HTTPException):
                    code = 302
                    description = 'Session expired, redirecting to login'
                    
                    def get_response(self, environ=None):
                        return redirect(url_for('Airflow.login', _external=True))
                
                # Raise the redirect exception
                raise RedirectToLogin()
            
            return False
        
        user = refreshed
    
    method_str = method.value if hasattr(method, "value") else str(method)
    if method_str.upper() == "GET" and resource_id is None:
        method_str = "LIST"
    
    roles = self._extract_roles_from_token(user.access_token)
    return self._role_allows(roles, resource_type, method_str)
