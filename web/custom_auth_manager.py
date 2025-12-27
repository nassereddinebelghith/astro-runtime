# oidc_keycloak_auth_manager.py
# Complete OIDC AuthManager for Airflow — without UMA — with full RBAC matrix
# Updated to decode JWT using Keycloak JWKS public certificates.
# Modified to handle expired tokens gracefully and provide public access by default

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
    Replacement for the UMA-based KeycloakAuthManager.

    This version uses:
    - OIDC JWT groups
    - ROLES_MAPPING env var
    - local RBAC matrix
    - NO UMA calls
    - JWT decoding / verification against Keycloak JWKS public keys
    - Graceful handling of expired tokens
    - Public access by default (no 401 on initial visit)
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
            "ADMIN": {"+": {"+"}},
            
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
            
            # PUBLIC - Default role for unauthenticated users
            # Allow access to landing page and login endpoints only
            "PUBLIC": {
                "VIEW": {"GET"},  # Allow viewing the landing page
                "MENU": {"GET"},  # Allow seeing the menu to find login
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
        
        Returns the decoded claims if signature is valid.
        Raises AirflowException if invalid.
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
        """
        Check if token is expired by examining the 'exp' claim.
        Does not verify signature - use only for expiration check.
        """
        try:
            payload = OidcKeycloakAuthManager._decode_jwt_payload(token)
            exp = payload.get("exp")
            if exp is None:
                return True  # No expiration = treat as expired for safety
            return exp < int(time.time())
        except Exception:
            return True  # If can't decode, treat as expired

    def _extract_roles_from_token(self, token: str) -> Set[str]:
        """Extract roles from JWT token based on Keycloak groups."""
        claims = self._decode_jwt(token)

        groups = claims.get("groups", []) or []
        roles: Set[str] = set()
        for group in groups:
            # Split by comma in case group contains multiple values
            split = group.split(",")

            if split[0] in self.roles_mapping or split[1] in self.roles_mapping:
                roles.update(self.roles_mapping[split[1]])

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
            if "+" in matrix:
                return True

            allowed = matrix.get(res)
            if allowed and ("+" in allowed or method_up in allowed):
                return True

        return False

    def is_logged_in(self) -> bool:
        """
        Check if user is logged in.
        
        MODIFICATION: Return True by default to avoid 401 on landing page.
        Only return False if we explicitly know auth is required and user is not logged in.
        """
        try:
            user = self.get_user()
            if not user:
                # No user in session - allow public access
                return True  # Changed from False to True
            
            # User exists - check if token is valid
            if hasattr(user, 'access_token') and user.access_token:
                if self._token_expired(user.access_token):
                    log.info("User token expired, will attempt refresh")
                    return False
                return True
            
            # User exists but no token - allow access (shouldn't happen but be safe)
            return True
            
        except Exception as e:
            log.warning(f"Error checking login status: {e}")
            # On error, allow access (public by default)
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
        Authorization check with graceful token expiration handling.
        
        MODIFICATION: 
        - Grant public access if no user or expired token
        - Attempt token refresh if expired
        - Fall back to PUBLIC role permissions if auth fails
        """
        # If no user, grant PUBLIC role permissions
        if not user:
            log.debug("No user found, granting PUBLIC role permissions")
            return self._role_allows({"PUBLIC"}, resource_type, method)
        
        # Check if token is expired
        if hasattr(user, 'access_token') and user.access_token:
            if self._token_expired(user.access_token):
                log.info("Access token expired for user, attempting refresh")
                try:
                    # Attempt to refresh the token
                    refreshed = self.refresh_user(user=user)
                    if refreshed:
                        user = refreshed
                    else:
                        # Refresh failed - grant PUBLIC access
                        log.warning("Token refresh failed, granting PUBLIC access")
                        return self._role_allows({"PUBLIC"}, resource_type, method)
                except Exception as e:
                    # Refresh raised exception - grant PUBLIC access
                    log.error(f"Error refreshing token: {e}, granting PUBLIC access")
                    return self._role_allows({"PUBLIC"}, resource_type, method)
        
        # Convert method to string if needed
        method_str = method.value if hasattr(method, "value") else str(method)

        # Special case: GET on "/" for landing page - always allow
        if method_str.upper() == "GET" and resource_id is None:
            return True

        # Extract roles from token
        try:
            roles = self._extract_roles_from_token(user.access_token)
        except Exception as e:
            log.error(f"Failed to extract roles from token: {e}, granting PUBLIC access")
            return self._role_allows({"PUBLIC"}, resource_type, method)
        
        # Check authorization based on roles
        return self._role_allows(roles, resource_type, method_str)

    def _is_batch_authorized(self, *, permissions, user):
        """
        Batch authorization check with token expiration handling.
        """
        if not user:
            # No user - grant PUBLIC access to all
            return set()
        
        # Check token expiration
        if hasattr(user, 'access_token') and user.access_token:
            if self._token_expired(user.access_token):
                try:
                    refreshed = self.refresh_user(user=user)
                    if refreshed:
                        user = refreshed
                    else:
                        log.warning("Token refresh failed in batch auth, returning empty set")
                        return set()
                except Exception as e:
                    log.error(f"Error refreshing token in batch auth: {e}")
                    return set()

        # Extract roles
        try:
            roles = self._extract_roles_from_token(user.access_token)
        except Exception as e:
            log.error(f"Failed to extract roles in batch auth: {e}")
            return set()

        allowed = set()
        for method, res in permissions:
            if self._role_allows(roles, KeycloakResource.MENU, str(method)):
                allowed.add((method, res))

        return allowed

    def refresh_user(self, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
        """
        Refresh user tokens with graceful error handling.
        
        MODIFICATION:
        - Catch KeycloakPostError for invalid_grant
        - Return None instead of raising exception
        - Log warning instead of error
        """
        try:
            from keycloak.exceptions import KeycloakPostError
            
            # Attempt parent's refresh logic
            return super().refresh_user(user=user)
            
        except KeycloakPostError as e:
            error_str = str(e)
            
            # Check for token expiration errors
            if any(keyword in error_str for keyword in [
                "invalid_grant",
                "Token is not active",
                "token_expired",
                "invalid_token"
            ]):
                log.warning(
                    f"Refresh token expired for user {user.username if hasattr(user, 'username') else 'unknown'}: {error_str}"
                )
                # Clear session gracefully
                self._clear_expired_session()
                return None
            
            # Other Keycloak error
            log.error(f"Keycloak error during token refresh: {error_str}")
            return None
            
        except Exception as e:
            log.error(f"Unexpected error during token refresh: {e}")
            return None

    def _clear_expired_session(self):
        """
        Clear expired session data without redirecting.
        
        This allows the public access logic to take over naturally.
        """
        try:
            from flask import session
            
            log.info("Clearing expired session data")
            
            # Clear session
            session.clear()
            
            # Note: We don't redirect here - we let the public access logic
            # handle the request naturally
            
        except Exception as e:
            log.error(f"Error clearing expired session: {e}")

    @staticmethod
    def _get_payload(*args, **kwargs):
        """Disable UMA payload generation"""
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        """Disable UMA batch payload generation"""
        raise AirflowException("UMA is disabled in this AuthManager.")
