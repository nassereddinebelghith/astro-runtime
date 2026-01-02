# custom_kc_auth_plugin.py

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


# -------------------------
# Role normalization
# -------------------------
def _normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value in {"admin", "administrator", "superadmin", "super_admin"}:
        return "ADMIN"
    if value in {"op", "operator"}:
        return "OP"
    if value == "user":
        return "USER"
    if value in {"viewer", "readonly", "read_only"}:
        return "VIEWER"
    return role.upper()


# -------------------------
# Auth Manager
# -------------------------
class OidcKeycloakAuthManager(KeycloakAuthManager):
    """
    Custom OIDC Keycloak AuthManager
    - NO UMA
    - JWT verification via JWKS
    - Explicit RBAC matrix
    """

    def __init__(self):
        super().__init__()

        # -------------------------
        # Load role mapping
        # -------------------------
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

        # -------------------------
        # RBAC Matrix (FULL)
        # -------------------------
        self.permissions_matrix = {
            "ADMIN": {"*": {"*"}},

            "OP": {
                "DAG": {"GET", "LIST", "POST", "PATCH"},
                "DAG_RUN": {"GET", "LIST", "POST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
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
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
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

            "VIEWER": {
                "DAG": {"GET", "LIST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
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

        # -------------------------
        # JWKS init
        # -------------------------
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY).rstrip("/")
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        self.jwks_url = (
            f"{server_url}/realms/{realm}/protocol/openid-connect/certs"
        )
        self.jwks_keys = self._load_jwks_keys()

    # -------------------------
    # JWKS
    # -------------------------
    def _load_jwks_keys(self) -> List[jwk.JWK]:
        resp = requests.get(self.jwks_url, timeout=5)
        resp.raise_for_status()

        keys = []
        for k in resp.json().get("keys", []):
            try:
                keys.append(jwk.JWK(**k))
            except Exception as exc:
                log.warning("Invalid JWKS key ignored: %s", exc)

        if not keys:
            raise AirflowException("No valid JWKS keys loaded from Keycloak")

        return keys

    # -------------------------
    # JWT helpers
    # -------------------------
    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        if token.startswith("Bearer "):
            token = token[7:]
        parts = token.split(".")
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(urlsafe_b64decode(payload_b64))

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

        raise AirflowException(f"JWT verification failed: {last_exc}")

    @staticmethod
    def _token_expired(token: str) -> bool:
        payload = OidcKeycloakAuthManager._decode_jwt_payload(token)
        exp = payload.get("exp")
        return exp is None or exp < int(time.time())

    # -------------------------
    # Role extraction
    # -------------------------
    def _extract_roles_from_token(self, token: str) -> Set[str]:
        claims = self._decode_jwt(token)
        roles: Set[str] = set()

        for group in claims.get("groups", []):
            parts = [p.strip() for p in group.split(",")]
            if len(parts) == 2 and parts[1] in self.roles_mapping:
                roles.update(self.roles_mapping[parts[1]])

        return roles

    # -------------------------
    # Authorization
    # -------------------------
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

    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:

        if self._token_expired(user.access_token):
            refreshed = self.refresh_user(user=user)
            if not refreshed:
                return False
            user = refreshed

        method_str = method.value if hasattr(method, "value") else str(method)
        if method_str.upper() == "GET" and resource_id is None:
            method_str = "LIST"

        roles = self._extract_roles_from_token(user.access_token)
        return self._role_allows(roles, resource_type, method_str)

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

    # -------------------------
    # UMA disabled (CRITICAL)
    # -------------------------
    @staticmethod
    def _get_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")