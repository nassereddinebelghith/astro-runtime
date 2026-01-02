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


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value in {"admin", "administrator", "superadmin"}:
        return "ADMIN"
    if value in {"op", "operator"}:
        return "OP"
    if value == "user":
        return "USER"
    if value in {"viewer", "readonly", "read_only"}:
        return "VIEWER"
    return role.upper()


# ------------------------------------------------------------------------------
# Auth Manager
# ------------------------------------------------------------------------------

class OidcKeycloakAuthManager(KeycloakAuthManager):
    """
    Custom OIDC Keycloak AuthManager for Airflow 3.x

    - NO UMA
    - STRICT role mapping (comma-based group format)
    - Stable RBAC
    - Safe token refresh handling
    """

    def __init__(self):
        super().__init__()

        # ------------------------------------------------------------------
        # Load role mapping (STRICT, unchanged contract)
        # ------------------------------------------------------------------
        raw_mapping = os.getenv("ROLES_MAPPING", "{}")
        try:
            parsed = json.loads(raw_mapping)
        except Exception as exc:
            raise AirflowException(
                f"Invalid ROLES_MAPPING JSON: {raw_mapping}"
            ) from exc

        self.roles_mapping: Dict[str, Set[str]] = {
            group: {_normalize_role(r) for r in roles}
            for group, roles in parsed.items()
        }

        log.info("Loaded ROLES_MAPPING: %s", self.roles_mapping)

        # ------------------------------------------------------------------
        # RBAC permissions matrix (UNCHANGED)
        # ------------------------------------------------------------------
        self.permissions_matrix = {
            "ADMIN": {"*": {"*"}},

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

        # ------------------------------------------------------------------
        # JWKS
        # ------------------------------------------------------------------
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY).rstrip("/")
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        self.jwks_url = (
            f"{server_url}/realms/{realm}/protocol/openid-connect/certs"
        )
        self.jwks_keys: List[jwk.JWK] = self._load_jwks_keys()

    # --------------------------------------------------------------------------
    # JWT helpers
    # --------------------------------------------------------------------------

    def _load_jwks_keys(self) -> List[jwk.JWK]:
        try:
            resp = requests.get(self.jwks_url, timeout=5)
            resp.raise_for_status()
            jwks_json = resp.json()
        except Exception as exc:
            raise AirflowException(
                f"Failed to fetch JWKS from {self.jwks_url}: {exc}"
            ) from exc

        keys: List[jwk.JWK] = []
        for k in jwks_json.get("keys", []):
            try:
                keys.append(jwk.JWK(**k))
            except Exception as exc:
                log.warning("Invalid JWKS key skipped: %s", exc)

        if not keys:
            raise AirflowException("No valid JWKS keys loaded from Keycloak")

        return keys

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        if token.startswith("Bearer "):
            token = token[7:]
        parts = token.split(".")
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(urlsafe_b64decode(padded))

    def _decode_jwt(self, token: str) -> dict:
        if token.startswith("Bearer "):
            token = token[7:]

        last_exc = None
        for key in self.jwks_keys:
            try:
                parsed = jwcrypto_jwt.JWT(
                    jwt=token,
                    key=key,
                    algs=["RS256", "HS256"],
                    check_claims=False,
                )
                return json.loads(parsed.claims)
            except Exception as exc:
                last_exc = exc

        raise AirflowException(
            f"Failed to verify JWT using JWKS keys: {last_exc}"
        )

    @staticmethod
    def _token_expired(token: str) -> bool:
        payload = OidcKeycloakAuthManager._decode_jwt_payload(token)
        exp = payload.get("exp")
        return not exp or exp < int(time.time())

    # --------------------------------------------------------------------------
    # ROLE EXTRACTION — STRICT CONTRACT (comma-based)
    # --------------------------------------------------------------------------

    def _extract_roles_from_token(self, token: str) -> Set[str]:
        claims = self._decode_jwt(token)
        groups = claims.get("groups", []) or []

        roles: Set[str] = set()

        for group in groups:
            if not isinstance(group, str):
                continue
            if "," not in group:
                continue

            _, role_key = group.split(",", 1)
            role_key = role_key.strip()

            if role_key in self.roles_mapping:
                roles.update(self.roles_mapping[role_key])

        return roles

    # --------------------------------------------------------------------------
    # RBAC evaluation
    # --------------------------------------------------------------------------

    def _role_allows(
        self,
        roles: Set[str],
        resource_type: KeycloakResource,
        method: str,
    ) -> bool:
        res = resource_type.value
        method_up = method.upper()

        for role in roles:
            matrix = self.permissions_matrix.get(role)
            if not matrix:
                continue

            if "*" in matrix:
                return True

            allowed = matrix.get(res)
            if allowed and ("*" in allowed or method_up in allowed):
                return True

        return False

    # --------------------------------------------------------------------------
    # AUTH FLOW FIXES (token refresh, no 500)
    # --------------------------------------------------------------------------

    def get_user(self) -> KeycloakAuthManagerUser | None:
        user = super().get_user()
        if not user:
            return None

        if user.access_token and self._token_expired(user.access_token):
            refreshed = self.refresh_user(user=user)
            if refreshed:
                return refreshed
            return None

        return user

    def refresh_user(
        self, user: KeycloakAuthManagerUser
    ) -> KeycloakAuthManagerUser | None:
        try:
            return super().refresh_user(user=user)
        except Exception as exc:
            log.warning("Token refresh failed: %s", exc)
            self._safe_clear_session()
            return None

    def _safe_clear_session(self) -> None:
        try:
            from flask import session, has_request_context
            if has_request_context():
                session.clear()
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # AUTHORIZATION
    # --------------------------------------------------------------------------

    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:
        if not user:
            return False

        method_str = method.value if hasattr(method, "value") else str(method)
        roles = self._extract_roles_from_token(user.access_token)
        return self._role_allows(roles, resource_type, method_str)

    def _is_batch_authorized(self, *, permissions, user):
        if self._token_expired(user.access_token):
            user = self.refresh_user(user=user)
            if not user:
                return set()

        roles = self._extract_roles_from_token(user.access_token)
        allowed = set()

        for method, res in permissions:
            method_str = method.value if hasattr(method, "value") else str(method)
            if self._role_allows(roles, res, method_str):
                allowed.add((method, res))

        return allowed

    # --------------------------------------------------------------------------
    # UMA disabled
    # --------------------------------------------------------------------------

    @staticmethod
    def _get_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager")