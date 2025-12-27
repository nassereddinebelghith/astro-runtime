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
            if "*" in matrix:
                return True

            allowed = matrix.get(res)
            if allowed and ("*" in allowed or method_up in allowed):
                return True

        return False

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
                # Return None to signal that refresh failed
                return None
            else:
                log.error(f"Keycloak error during token refresh: {error_str}")
                return None
                
        except Exception as e:
            log.error(f"Unexpected error during token refresh: {e}")
            return None

    # ==========================================================================
    # MODIFICATION 1 : Permettre accès public (pas de 401 sur landing page)
    # ==========================================================================
    def is_logged_in(self) -> bool:
        """Check if user is logged in - MODIFIÉ pour permettre accès public"""
        try:
            user = self.get_user()
            if not user:
                # CHANGEMENT : return True au lieu de False
                return True  # Permet accès public à la landing page
            
            if hasattr(user, 'access_token') and user.access_token:
                if self._token_expired(user.access_token):
                    return False
                return True
            
            return True
        except Exception:
            return True  # Permet accès public en cas d'erreur

    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:
        # Permettre accès public à VIEW (landing page)
        if not user:
            method_str = method.value if hasattr(method, "value") else str(method)
            if resource_type.value == "VIEW" and method_str.upper() == "GET":
                return True
            return False
        
        # ==========================================================================
        # Token expiration handling - simplified since refresh_user catches errors
        # ==========================================================================
        if hasattr(user, 'access_token') and user.access_token:
            if self._token_expired(user.access_token):
                log.info("Access token expired, attempting refresh")
                refreshed = self.refresh_user(user=user)
                
                if refreshed:
                    # Refresh succeeded, use new tokens
                    user = refreshed
                    log.info("Token successfully refreshed")
                else:
                    # Refresh failed (invalid_grant caught in refresh_user)
                    log.warning("Token refresh failed, re-authentication needed")
                    return False

        # Reste du code IDENTIQUE à l'original
        method_str = method.value if hasattr(method, "value") else str(method)

        # Extract roles and check permissions
        roles = self._extract_roles_from_token(user.access_token)
        return self._role_allows(roles, resource_type, method_str)

    def _is_batch_authorized(self, *, permissions, user):
        if self._token_expired(user.access_token):
            refreshed = self.refresh_user(user=user)
            if refreshed:
                user = refreshed
            else:
                return set()

        roles = self._extract_roles_from_token(user.access_token)
        
        allowed = set()
        for method, res in permissions:
            if self._role_allows(roles, KeycloakResource.MENU, str(method)):
                allowed.add((method, res))

        return allowed

    @staticmethod
    def _get_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")
