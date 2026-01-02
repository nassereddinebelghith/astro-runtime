# custom_kc_auth_plugin.py
# OIDC Keycloak AuthManager for Airflow
# - JWT verification via JWKS
# - No UMA
# - Stable RBAC mapping
# - Safe handling of expired / invalid tokens (NO 500)

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


# -------------------------------------------------------------------
# Role normalization (DO NOT CHANGE LOGIC)
# -------------------------------------------------------------------
def _normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value in {"admin", "administrator", "superadmin", "super_admin"}:
        return "ADMIN"
    if value in {"op", "operator", "ops"}:
        return "OP"
    if value in {"user", "developer", "dev"}:
        return "USER"
    if value in {"viewer", "read", "readonly", "read_only"}:
        return "VIEWER"
    return role.upper()


# -------------------------------------------------------------------
# Auth Manager
# -------------------------------------------------------------------
class OidcKeycloakAuthManager(KeycloakAuthManager):
    """
    Custom Keycloak AuthManager:
    - JWT groups based RBAC
    - NO UMA
    - NO internal server error on invalid/expired token
    """

    def __init__(self):
        super().__init__()

        # ------------------------------------------------------------
        # Load role mapping from env (DO NOT CHANGE FORMAT)
        # ------------------------------------------------------------
        raw_mapping = os.getenv("ROLES_MAPPING", "{}")
        try:
            parsed = json.loads(raw_mapping)
        except Exception as exc:
            raise AirflowException(
                f"Invalid ROLES_MAPPING JSON: {raw_mapping}"
            ) from exc

        self.roles_mapping: Dict[str, Set[str]] = {}
        for group, roles in parsed.items():
            self.roles_mapping[group] = {_normalize_role(r) for r in roles}

        log.info("Loaded ROLES_MAPPING: %s", self.roles_mapping)

        # ------------------------------------------------------------
        # COMPLETE RBAC MATRIX (SAFE & EXHAUSTIVE)
        # ------------------------------------------------------------
        self.permissions_matrix = {
            # FULL ACCESS
            "ADMIN": {"*": {"*"}},

            # OPERATIONS
            "OP": {
                "DAG": {"GET", "LIST", "PATCH"},
                "DAG_RUN": {"GET", "LIST", "POST", "PATCH"},
                "TASK_INSTANCE": {"GET", "LIST", "PATCH"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "EVENT_LOG": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "VARIABLE": {"GET", "LIST"},
                "CONNECTION": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "PLUGIN": {"GET", "LIST"},
                "PROVIDER": {"GET", "LIST"},
                "VERSION": {"GET"},
                "HEALTH": {"GET"},
                "CONFIG": {"GET"},
                "VIEW": {"GET"},
                "MENU": {"GET"},
                "CUSTOM": {"GET"},
            },

            # DEVELOPERS
            "USER": {
                "DAG": {"GET", "LIST"},
                "DAG_RUN": {"GET", "LIST", "POST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "EVENT_LOG": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "VARIABLE": {"GET", "LIST"},
                "CONNECTION": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "PLUGIN": {"GET", "LIST"},
                "PROVIDER": {"GET", "LIST"},
                "VERSION": {"GET"},
                "HEALTH": {"GET"},
                "CONFIG": {"GET"},
                "VIEW": {"GET"},
                "MENU": {"GET"},
                "CUSTOM": {"GET"},
            },

            # READ-ONLY
            "VIEWER": {
                "DAG": {"GET", "LIST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "EVENT_LOG": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "VARIABLE": {"GET", "LIST"},
                "CONNECTION": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "PLUGIN": {"GET", "LIST"},
                "PROVIDER": {"GET", "LIST"},
                "VERSION": {"GET"},
                "HEALTH": {"GET"},
                "CONFIG": {"GET"},
                "VIEW": {"GET"},
                "MENU": {"GET"},
                "CUSTOM": {"GET"},
            },
        }

        # ------------------------------------------------------------
        # JWKS init
        # ------------------------------------------------------------
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY).rstrip("/")
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        self.jwks_url = f"{server_url}/realms/{realm}/protocol/openid-connect/certs"
        self.jwks_keys = self._load_jwks_keys()

    # ----------------------------------------------------------------
    # JWKS
    # ----------------------------------------------------------------
    def _load_jwks_keys(self) -> List[jwk.JWK]:
        try:
            resp = requests.get(self.jwks_url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise AirflowException(
                f"Failed to fetch Keycloak JWKS: {exc}"
            ) from exc

        keys: List[jwk.JWK] = []
        for raw in data.get("keys", []):
            try:
                keys.append(jwk.JWK(**raw))
            except Exception as exc:
                log.warning("Invalid JWKS key skipped: %s", exc)

        if not keys:
            raise AirflowException("No valid JWKS keys loaded")

        return keys

    # ----------------------------------------------------------------
    # JWT helpers
    # ----------------------------------------------------------------
    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        if token.startswith("Bearer "):
            token = token[7:]

        parts = token.split(".")
        if len(parts) != 3:
            raise AirflowException("Invalid JWT format")

        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(urlsafe_b64decode(payload))

    def _token_expired(self, token: str) -> bool:
        try:
            payload = self._decode_jwt_payload(token)
            exp = payload.get("exp")
            return exp is None or exp < int(time.time())
        except Exception:
            return True

    def _decode_jwt(self, token: str) -> dict:
        if token.startswith("Bearer "):
            token = token[7:]

        last_exc = None
        for key in self.jwks_keys:
            try:
                parsed = jwcrypto_jwt.JWT(
                    algs=["RS256", "HS256"],
                    jwt=token,
                    key=key,
                    check_claims=False,
                )
                return json.loads(parsed.claims)
            except Exception as exc:
                last_exc = exc
        raise AirflowException(f"JWT verification failed: {last_exc}")

    # ----------------------------------------------------------------
    # Role extraction (UNCHANGED BEHAVIOR)
    # ----------------------------------------------------------------
    def _extract_roles_from_token(self, token: str) -> Set[str]:
        claims = self._decode_jwt(token)
        groups = claims.get("groups") or []
        roles: Set[str] = set()

        for group in groups:
            parts = group.split(",")
            for part in parts:
                if part in self.roles_mapping:
                    roles.update(self.roles_mapping[part])

        return roles

    # ----------------------------------------------------------------
    # RBAC check
    # ----------------------------------------------------------------
    def _role_allows(
        self, roles: Set[str], resource: KeycloakResource, method: str
    ) -> bool:
        res = resource.value
        method = method.upper()

        for role in roles:
            matrix = self.permissions_matrix.get(role)
            if not matrix:
                continue
            if "*" in matrix:
                return True
            allowed = matrix.get(res)
            if allowed and ("*" in allowed or method in allowed):
                return True
        return False

    # ----------------------------------------------------------------
    # AUTHORIZATION (FIXED: NO 500)
    # ----------------------------------------------------------------
    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:

        # SAFE PUBLIC ENDPOINTS
        if resource_type.value in {"HEALTH", "VERSION"} and method.value == "GET":
            return True

        try:
            if self._token_expired(user.access_token):
                refreshed = self.refresh_user(user=user)
                if not refreshed:
                    log.warning("Access token expired and refresh failed")
                    return False
                user = refreshed

            method_str = method.value
            if method_str == "GET" and resource_id is None:
                method_str = "LIST"

            roles = self._extract_roles_from_token(user.access_token)
            return self._role_allows(roles, resource_type, method_str)

        except Exception as exc:
            # 🔥 THIS IS THE FIX: NO INTERNAL SERVER ERROR
            log.error("Authorization failure handled safely: %s", exc)
            return False

    # ----------------------------------------------------------------
    # Batch authorization
    # ----------------------------------------------------------------
    def _is_batch_authorized(self, *, permissions, user):
        if self._token_expired(user.access_token):
            refreshed = self.refresh_user(user=user)
            if not refreshed:
                return set()
            user = refreshed

        roles = self._extract_roles_from_token(user.access_token)
        allowed = set()

        for method, res in permissions:
            if self._role_allows(roles, KeycloakResource.MENU, str(method)):
                allowed.add((method, res))
        return allowed

    # ----------------------------------------------------------------
    # UMA DISABLED
    # ----------------------------------------------------------------
    @staticmethod
    def _get_payload(*_, **__):
        raise AirflowException("UMA is disabled in this AuthManager")

    @staticmethod
    def _get_batch_payload(*_, **__):
        raise AirflowException("UMA is disabled in this AuthManager")