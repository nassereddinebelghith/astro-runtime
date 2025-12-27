# oidc_keycloak_auth_manager.py
# Complete OIDC AuthManager for Airflow — without UMA — with full RBAC matrix
# FIXED VERSION: Properly handles expired tokens with redirect to landing page

from __future__ import annotations

import json
import logging
import os
import time
from base64 import urlsafe_b64decode
from typing import Dict, Set, List

import requests
from jwcrypto import jwk, jwt as jwcrypto_jwt
from flask import session, redirect, make_response

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
    """Normalize role names to uppercase standard format"""
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
    Enhanced KeycloakAuthManager with proper expired token handling.
    
    Key features:
    - Handles expired tokens gracefully (no 500 errors)
    - Automatic redirect to re-authentication when needed
    - Public access for landing page
    - Proper session cleanup
    """

    def __init__(self):
        super().__init__()

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
            
            # Operator role
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
            
            # Regular user
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
            
            # Viewer
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
            
            # PUBLIC - Default role for unauthenticated users
            "PUBLIC": {
                "VIEW": {"GET"},
                "MENU": {"GET"},
            },
        }

        # Build JWKS URL
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY)
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        server_url = server_url.rstrip("/")
        self.jwks_url = f"{server_url}/realms/{realm}/protocol/openid-connect/certs"

        # Load JWKS keys
        self.jwks_keys: List[jwk.JWK] = self._load_jwks_keys()

    def _load_jwks_keys(self) -> List[jwk.JWK]:
        """Load Keycloak JWKS keys"""
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
                log.warning("Failed to import a JWKS key: %s", exc)

        if not keys:
            raise AirflowException("No valid JWKS keys loaded from Keycloak.")

        log.info("Loaded %d JWKS public keys from Keycloak", len(keys))
        return keys

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        """Decode JWT payload without verifying"""
        try:
            if token.startswith("Bearer "):
                token = token[7:]
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError("Invalid JWT format")
            payload_b64 = parts[1]
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            return json.loads(urlsafe_b64decode(padded))
        except Exception as exc:
            raise AirflowException(f"Failed to decode JWT: {exc}") from exc

    def _decode_jwt(self, token: str) -> dict:
        """Decode and verify JWT"""
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

        raise AirflowException(f"Failed to verify JWT: {last_exc}")

    @staticmethod
    def _token_expired(token: str) -> bool:
        """Check if token is expired"""
        try:
            payload = OidcKeycloakAuthManager._decode_jwt_payload(token)
            exp = payload.get("exp")
            if exp is None:
                return True
            return exp < int(time.time())
        except Exception:
            return True

    def _extract_roles_from_token(self, token: str) -> Set[str]:
        """Extract roles from JWT"""
        claims = self._decode_jwt(token)
        groups = claims.get("groups", []) or []
        roles: Set[str] = set()
        
        for group in groups:
            # Remove leading slash if present
            group_clean = group.lstrip("/")
            
            # Check in mapping
            if group_clean in self.roles_mapping:
                roles.update(self.roles_mapping[group_clean])
                log.debug(f"Group '{group_clean}' mapped to roles: {self.roles_mapping[group_clean]}")

        if not roles:
            log.warning(f"No roles found for groups: {groups}")
        
        return roles

    def _role_allows(self, roles: Set[str], resource_type: KeycloakResource, method: str) -> bool:
        """Check if any role allows the resource/method"""
        res = resource_type.value
        method_up = method.upper()

        for role in roles:
            matrix = self.permissions_matrix.get(role)
            if not matrix:
                continue

            # Wildcard permission
            if "*" in matrix:
                return True

            allowed = matrix.get(res)
            if allowed and ("*" in allowed or method_up in allowed):
                return True

        return False

    def is_logged_in(self) -> bool:
        """Check if user is logged in - allow public access by default"""
        try:
            user = self.get_user()
            if not user:
                return True  # Public access
            
            if hasattr(user, 'access_token') and user.access_token:
                if self._token_expired(user.access_token):
                    log.info("User token expired")
                    return False
                return True
            
            return True
            
        except Exception as e:
            log.warning(f"Error checking login status: {e}")
            return True

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
        Authorization check with proper expired token handling.
        
        KEY FIX: When token is expired and refresh fails,
        return False to trigger OAuth flow (not PUBLIC access)
        """
        # No user → PUBLIC permissions
        if not user:
            log.debug("No user found, granting PUBLIC role permissions")
            is_allowed = self._role_allows({"PUBLIC"}, resource_type, method.value if hasattr(method, "value") else str(method))
            if is_allowed:
                return True
            # PUBLIC doesn't allow - return False to trigger OAuth
            log.info("PUBLIC role doesn't allow access, OAuth flow will be triggered")
            return False
        
        # Check if token expired
        if hasattr(user, 'access_token') and user.access_token:
            if self._token_expired(user.access_token):
                log.warning("Access token expired for user, attempting refresh")
                try:
                    from keycloak.exceptions import KeycloakPostError
                    
                    # Attempt refresh
                    refreshed = super().refresh_user(user=user)
                    if refreshed:
                        user = refreshed
                        log.info("Token successfully refreshed")
                    else:
                        # Refresh returned None - trigger re-auth
                        log.warning("Token refresh returned None, triggering re-auth")
                        self._handle_expired_token()
                        return False
                        
                except KeycloakPostError as e:
                    error_str = str(e)
                    if any(keyword in error_str for keyword in ["invalid_grant", "Token is not active"]):
                        log.warning(f"Refresh token expired: {error_str}")
                        self._handle_expired_token()
                        return False
                    else:
                        log.error(f"Keycloak error: {error_str}")
                        self._handle_expired_token()
                        return False
                        
                except Exception as e:
                    log.error(f"Error refreshing token: {e}")
                    self._handle_expired_token()
                    return False
        
        # Extract roles
        try:
            roles = self._extract_roles_from_token(user.access_token)
        except Exception as e:
            log.error(f"Failed to extract roles: {e}")
            self._handle_expired_token()
            return False
        
        # No roles → Trigger re-auth
        if not roles:
            log.warning("User has no roles, triggering re-auth")
            self._handle_expired_token()
            return False
        
        # Check permissions
        method_str = method.value if hasattr(method, "value") else str(method)
        return self._role_allows(roles, resource_type, method_str)

    def _handle_expired_token(self):
        """
        Handle expired token by clearing session.
        
        The return False from _is_authorized will trigger
        Airflow's normal OAuth flow automatically.
        """
        try:
            log.info("Handling expired token - clearing session")
            session.clear()
            # Don't redirect here - let Airflow's OAuth flow handle it
        except Exception as e:
            log.error(f"Error handling expired token: {e}")

    def _is_batch_authorized(self, *, permissions, user):
        """Batch authorization"""
        if not user:
            return set()
        
        if hasattr(user, 'access_token') and user.access_token:
            if self._token_expired(user.access_token):
                try:
                    refreshed = super().refresh_user(user=user)
                    if refreshed:
                        user = refreshed
                    else:
                        return set()
                except Exception:
                    return set()

        try:
            roles = self._extract_roles_from_token(user.access_token)
        except Exception:
            return set()

        allowed = set()
        for method, res in permissions:
            if self._role_allows(roles, KeycloakResource.MENU, str(method)):
                allowed.add((method, res))

        return allowed

    @staticmethod
    def _get_payload(*args, **kwargs):
        """UMA disabled"""
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        """UMA disabled"""
        raise AirflowException("UMA is disabled in this AuthManager.")
