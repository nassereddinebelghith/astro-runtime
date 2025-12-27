# oidc_keycloak_auth_manager.py
# TON CODE ORIGINAL + 2 MODIFICATIONS CHIRURGICALES SEULEMENT

from __future__ import annotations

import json
import logging
import os
import time
from base64 import urlsafe_b64decode
from typing import Dict, Set, List

import requests
from jwcrypto import jwk, jwt as jwcrypto_jwt

from airflow.configuration import conf
from airflow.exceptions import AirflowException
from airflow.providers.keycloak.auth_manager.constants import (
    CONF_REALM_KEY,
    CONF_SERVER_URL_KEY,
    CONF_SECTION_NAME,
)
from airflow.providers.keycloak.auth_manager.keycloak_auth_manager import (
    KeycloakAuthManager,
    KeycloakResource,
)
from airflow.providers.keycloak.auth_manager.user import KeycloakAuthManagerUser

log = logging.getLogger(__name__)


def _normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value in {"admin", "administrator"}:
        return "ADMIN"
    if value in {"op", "operator"}:
        return "OP"
    if value == "user":
        return "USER"
    if value in {"viewer", "readonly", "read_only"}:
        return "VIEWER"
    return role.upper()


class OidcKeycloakAuthManager(KeycloakAuthManager):
    """
    Replacement for the UMA-based KeycloakAuthManager.
    
    This version uses:
    - OIDC JWT groups
    - ROLES_MAPPING env var
    - local RBAC matrix
    - NO UMA calls
    - JWT decoding / verification against Keycloak JWKS public keys
    """

    def __init__(self):
        super().__init__()

        # Optional defaults (safe-by-default):
        # - DEFAULT_ROLE: applied when user is authenticated but has no mapped roles.
        # - PUBLIC_LANDING: if 'true', allow unauthenticated GET on '/' and '/home' only.
        self.default_role = _normalize_role(os.getenv("DEFAULT_ROLE", "VIEWER"))
        self.public_landing = os.getenv("PUBLIC_LANDING", "false").strip().lower() == "true"

        # Load role mapping from env
        raw_mapping = os.getenv("ROLES_MAPPING", "{}")
        try:
            parsed = json.loads(raw_mapping)
        except Exception as exc:
            raise AirflowException(f"Invalid ROLES_MAPPING JSON: {raw_mapping}") from exc

        self.roles_mapping: Dict[str, Set[str]] = {}
        for group, roles in parsed.items():
            self.roles_mapping[group] = {_normalize_role(r) for r in roles}

        log.info("Loaded ROLES_MAPPING: %s", self.roles_mapping)

        # Permissions matrix for internal RBAC
        self.permissions_matrix = {
            # Full access
            "ADMIN": {"*": {"*"}},
            
            # Operator role — run DAGs, view logs, manage runs
            "OP": {
                "DAG": {"GET", "LIST", "POST", "PATCH"},
                "DAG_RUN": {"GET", "LIST", "POST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "VARIABLE": {"GET", "LIST"},
                "CONNECTION": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "VIEW": {"GET"},
                "CUSTOM": {"GET", "POST"},
                "MENU": {"GET"},
            },
            
            # Regular user — can see DAGs and trigger them
            "USER": {
                "DAG": {"GET", "LIST", "POST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "VARIABLE": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "VIEW": {"GET"},
                "CUSTOM": {"GET"},
                "MENU": {"GET"},
            },
            
            # Viewer — read-only everywhere
            "VIEWER": {
                "DAG": {"GET", "LIST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "VARIABLE": {"GET", "LIST"},
                "CONNECTION": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "VIEW": {"GET"},
                "CUSTOM": {"GET"},
                "MENU": {"GET"},
            },
        }

        # Build JWKS URL from Airflow Keycloak config
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY)
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        # Ensure no trailing slash on server_url
        server_url = server_url.rstrip("/")
        self.jwks_url = f"{server_url}/realms/{realm}/protocol/openid-connect/certs"

        # Load JWKS public keys from Keycloak
        self.jwks_keys: List[jwk.JWK] = self._load_jwks_keys()

    def _load_jwks_keys(self) -> List[jwk.JWK]:
        """Load Keycloak JWKS keys used to verify RS256 JWT signatures."""
        try:
            resp = requests.get(self.jwks_url, timeout=5)
            resp.raise_for_status()
            jwks_json = resp.json()
        except Exception as exc:
            raise AirflowException(f"Failed to fetch Keycloak JWKS from {self.jwks_url}: {exc}") from exc

        keys: List[jwk.JWK] = []
        for k in jwks_json.get("keys", []):
            try:
                key = jwk.JWK(**k)
                keys.append(key)
            except Exception as exc:
                log.warning("Failed to import a JWKS key from Keycloak: %s", exc)

        if not keys:
            raise AirflowException("No valid JWKS keys loaded from Keycloak.")

        log.info("Loaded %d JWKS public keys from Keycloak", len(keys))
        return keys

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        """Decode JWT payload without verifying signature (used only for exp check)."""
        try:
            # Remove possible Bearer prefix
            if token.startswith("Bearer "):
                token = token[7:]

            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError("Invalid JWT format (expected 3 parts)")

            payload_b64 = parts[1]
            # Add padding if needed
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            return json.loads(urlsafe_b64decode(padded))
        except Exception as exc:
            raise AirflowException(f"Failed to decode JWT payload: {exc}") from exc

    def _decode_jwt(self, token: str) -> dict:
        """
        Decode and verify a Keycloak HS256, RS256 JWT using JWKS public keys.
        """
        # Remove possible Bearer prefix
        if token.startswith("Bearer "):
            token = token[7:]

        last_exc: Exception | None = None

        for key in self.jwks_keys:
            try:
                parsed = jwcrypto_jwt.JWT(
                    algs=["HS256", "RS256"],
                    check_claims=False,
                    key=key,
                    jwt=token,
                )
                return json.loads(parsed.claims)
            except Exception as exc:
                last_exc = exc
                continue

        raise AirflowException(
            f"Failed to verify Keycloak JWT with JWKS keys: {last_exc}"
        )

    @staticmethod
    def _token_expired(token: str) -> bool:
        """Check if token is expired"""
        payload = OidcKeycloakAuthManager._decode_jwt_payload(token)
        exp = payload.get("exp")
        if exp is None:
            return True
        return exp < int(time.time())

    def _extract_roles_from_token(self, token: str) -> Set[str]:
        claims = self._decode_jwt(token)

        groups = claims.get("groups", []) or []
        roles: Set[str] = set()
        for group in groups:
            # Some IdPs provide groups as a list of strings; occasionally a single string may contain commas.
            # Be defensive (avoid IndexError) and accept any listed group token.
            for g in [p.strip() for p in str(group).split(",") if p.strip()]:
                mapped = self.roles_mapping.get(g)
                if mapped:
                    roles.update(mapped)

        return roles

    def _role_allows(
        self, 
        roles: Set[str], 
        resource_type: KeycloakResource, 
        method: str
    ) -> bool:
        """Check if any role in the set allows the given resource and method."""
        res = resource_type.value
        method_up = method.upper()

        for role in roles:
            matrix = self.permissions_matrix.get(role)
            if not matrix:
                continue

            # Check for wildcard permission (ADMIN)
            if "*" in matrix:
                return True

            allowed = matrix.get(res)
            if allowed and ("*" in allowed or method_up in allowed):
                return True

        return False

    def _safe_clear_session(self) -> None:
        """Best-effort session cleanup (never raise)."""
        try:
            from flask import has_request_context, session

            if not has_request_context():
                return

            # Common keys used by FAB/Airflow and custom plugins
            for k in ("_token", "token", "access_token", "refresh_token", "id_token", "userinfo"):
                session.pop(k, None)
            session.modified = True
        except Exception as exc:
            log.debug("Session cleanup skipped/failed: %s", exc)

    def _is_public_landing_path(self) -> bool:
        """Allow *only* a minimal landing page unauthenticated if enabled."""
        if not self.public_landing:
            return False
        try:
            from flask import has_request_context, request

            if not has_request_context():
                return False
            if request.method.upper() != "GET":
                return False
            return request.path in {"/", "/home"}
        except Exception:
            return False

    # ==========================================================================
    # CRITICAL: Override get_user to handle token expiration properly
    # ==========================================================================
    def get_user(self) -> KeycloakAuthManagerUser | None:
        """
        Override to return None when token is expired and refresh fails.
        This forces Airflow to treat user as logged out → triggers OAuth.
        """
        # Get user from parent
        user = super().get_user()
        
        if not user:
            return None
        
        # Check if token is expired
        if hasattr(user, 'access_token') and user.access_token:
            if self._token_expired(user.access_token):
                # Token expired - try to refresh
                log.debug("Token expired in get_user, attempting refresh")
                refreshed = self.refresh_user(user=user)
                
                if refreshed:
                    # Refresh succeeded - return new user
                    log.debug("Token refreshed successfully in get_user")
                    return refreshed
                else:
                    # Refresh failed - return None to trigger OAuth
                    log.info("Token refresh failed in get_user, clearing session and returning None to trigger OAuth")
                    self._safe_clear_session()
                    return None
        
        return user

    # ==========================================================================
    # CRITICAL FIX: Override refresh_user to catch KeycloakPostError at source
    # ==========================================================================
    def refresh_user(self, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
        """
        Override parent's refresh_user to catch KeycloakPostError BEFORE it propagates.
        
        This is THE critical fix that prevents 500 Internal Server Error.
        """
        try:
            from keycloak.exceptions import KeycloakPostError
            
            # Call parent's refresh_user (this is where KeycloakPostError is raised)
            return super().refresh_user(user=user)
            
        except KeycloakPostError as e:
            # CATCH the error BEFORE it becomes a 500
            error_str = str(e)
            if "invalid_grant" in error_str or "Token is not active" in error_str:
                log.warning(f"Refresh token expired (invalid_grant): {error_str}")
                self._safe_clear_session()
                # Return None to signal that refresh failed
                return None
            else:
                log.error(f"Keycloak error during token refresh: {error_str}")
                self._safe_clear_session()
                return None
                
        except Exception as e:
            log.error(f"Unexpected error during token refresh: {e}")
            self._safe_clear_session()
            return None

    # ==========================================================================
    # MODIFICATION 1 : is_logged_in() avec gestion intelligente
    # ==========================================================================
    def is_logged_in(self) -> bool:
        """
        Check if user is logged in.

        IMPORTANT: Do NOT return True when there is no authenticated user.
        Returning True without a user causes Airflow UI/API calls to proceed and end up as 403 instead
        of redirecting to Keycloak.

        Optional: PUBLIC_LANDING=true allows unauthenticated GET on '/' and '/home' only.
        """
        try:
            user = self.get_user()
            
            if not user:
                if self.public_landing:
                    try:
                        from flask import has_request_context, request
                        if has_request_context() and request.method == "GET" and request.path in {"/", "/home"}:
                            return True
                    except Exception:
                        pass
                return False
            
            # User exists - check if token is valid
            if hasattr(user, 'access_token') and user.access_token:
                if self._token_expired(user.access_token):
                    # Token expired → Return False to trigger OAuth
                    log.info("Token expired in is_logged_in, will trigger re-auth")
                    return False
                # Token valid
                return True
            
            # User exists but no token → Assume logged in
            return True
            
        except Exception as e:
            log.warning(f"Error in is_logged_in: {e}")
            # Fail closed for auth. (If you enable PUBLIC_LANDING, the landing check above is still safe.)
            return False

    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:
        # Unauthenticated users are not authorized (except optional public landing handled in is_logged_in).
        if not user:
            return False
        
        # User exists - get_user() already handled token refresh
        # Just check authorization based on roles
        method_str = method.value if hasattr(method, "value") else str(method)
        
        try:
            roles = self._extract_roles_from_token(user.access_token)
            if not roles:
                # Apply a safe default role for authenticated users with no mapped groups.
                # This prevents immediate Unauthorized/Forbidden after login when role mapping is incomplete.
                log.warning("User has no mapped roles; applying DEFAULT_ROLE=%s", self.default_role)
                roles = {self.default_role}
            return self._role_allows(roles, resource_type, method_str)
        except Exception as e:
            log.error(f"Error checking authorization: {e}")
            return False

    def _is_batch_authorized(self, *, permissions, user):
        if self._token_expired(user.access_token):
            refreshed = self.refresh_user(user=user)
            if refreshed:
                user = refreshed
            else:
                return set()

        roles = self._extract_roles_from_token(user.access_token)
        if not roles:
            log.warning("User has no mapped roles; applying DEFAULT_ROLE=%s", self.default_role)
            roles = {self.default_role}
        
        allowed = set()
        for method, res in permissions:
            method_str = method.value if hasattr(method, "value") else str(method)
            resource = res if isinstance(res, KeycloakResource) else KeycloakResource(res)
            if self._role_allows(roles, resource, method_str):
                allowed.add((method, res))

        return allowed

    @staticmethod
    def _get_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")
