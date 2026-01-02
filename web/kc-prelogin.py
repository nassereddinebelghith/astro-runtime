from __future__ import annotations

import json
import logging
import os
import time
from base64 import urlsafe_b64decode
from typing import Dict, List, Optional, Set, Tuple, Any

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
    v = (role or "").strip().lower()
    if v in {"admin", "administrator", "superadmin", "super_admin", "super-admin"}:
        return "ADMIN"
    if v in {"op", "operator"}:
        return "OP"
    if v == "user":
        return "USER"
    if v in {"viewer", "readonly", "read_only", "read-only"}:
        return "VIEWER"
    return (role or "").strip().upper()


class OidcKeycloakAuthManager(KeycloakAuthManager):
    """
    Hardened OIDC Keycloak AuthManager for Airflow 3.x

    Goals:
      - Strict group parsing: "id,group_name" => group_name == split[1]
      - Treat *_admin and *_superadmin as ADMIN
      - PRE-AUTH: allow UI to load with VIEWER (no 401/403 flash)
      - NEVER raise in any AuthManager entrypoint (prevents 500 Internal Server Error)
      - Token expiry/refresh:
          - refresh only in get_user()
          - refresh failures are caught + session cleared => triggers re-auth
    """

    def __init__(self) -> None:
        super().__init__()

        # Pre-auth role
        self.pre_auth_role: str = _normalize_role(os.getenv("PRE_AUTH_ROLE", "VIEWER"))
        # When authenticated but mapping returns empty, fallback role (safe)
        self.default_role: str = _normalize_role(os.getenv("DEFAULT_ROLE", "VIEWER"))

        # Load ROLES_MAPPING
        raw = os.getenv("ROLES_MAPPING", "{}")
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("ROLES_MAPPING must be a JSON object")
        except Exception as exc:
            raise AirflowException(f"Invalid ROLES_MAPPING JSON: {raw}") from exc

        self.roles_mapping: Dict[str, Set[str]] = {}
        for group_key, roles in parsed.items():
            if roles is None:
                continue
            if isinstance(roles, str):
                roles = [roles]
            self.roles_mapping[str(group_key)] = {_normalize_role(str(r)) for r in roles}

        # RBAC matrix (complete enough for UI/API)
        self.permissions_matrix: Dict[str, Dict[str, Set[str]]] = {
            # Full access
            "ADMIN": {"*": {"*"}},

            # Operator: run/monitor
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
                "XCOM": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "VIEW": {"GET"},
                "MENU": {"GET"},
                "CUSTOM": {"GET", "POST"},
            },

            # User: trigger/view
            "USER": {
                "DAG": {"GET", "LIST", "POST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "XCOM": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "VIEW": {"GET"},
                "MENU": {"GET"},
                "CUSTOM": {"GET"},
            },

            # Viewer: read-only
            "VIEWER": {
                "DAG": {"GET", "LIST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "XCOM": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "VIEW": {"GET"},
                "MENU": {"GET"},
            },
        }

        # JWKS
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY).rstrip("/")
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        self.jwks_url = f"{server_url}/realms/{realm}/protocol/openid-connect/certs"

        # JWKS keys loaded at init; if it fails, we still want the pod to start.
        # So: fail "soft" by setting empty list and logging; verification will then fail safe (deny).
        self.jwks_keys: List[jwk.JWK] = []
        try:
            self.jwks_keys = self._load_jwks_keys()
        except Exception as exc:
            log.error("JWKS load failed at init (will deny until available): %s", exc)
            self.jwks_keys = []

    # -------------------------------------------------------------------------
    # JWKS / JWT
    # -------------------------------------------------------------------------

    def _load_jwks_keys(self) -> List[jwk.JWK]:
        resp = requests.get(self.jwks_url, timeout=5)
        resp.raise_for_status()
        keys_json = resp.json().get("keys", []) or []
        keys: List[jwk.JWK] = []
        for k in keys_json:
            try:
                keys.append(jwk.JWK(**k))
            except Exception as exc:
                log.warning("Skipping invalid JWKS key: %s", exc)
        if not keys:
            raise AirflowException("No JWKS keys loaded")
        return keys

    @staticmethod
    def _decode_payload_no_verify(token: str) -> dict:
        token = (token or "").strip()
        if token.startswith("Bearer "):
            token = token[7:]
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(urlsafe_b64decode(padded))

    def _verify_and_decode_jwt(self, token: str) -> dict:
        token = (token or "").strip()
        if token.startswith("Bearer "):
            token = token[7:]

        # If no JWKS loaded, attempt reload once
        if not self.jwks_keys:
            try:
                self.jwks_keys = self._load_jwks_keys()
            except Exception as exc:
                raise AirflowException(f"JWKS unavailable: {exc}") from exc

        last_exc: Optional[Exception] = None
        for key in self.jwks_keys:
            try:
                parsed = jwcrypto_jwt.JWT(
                    jwt=token,
                    key=key,
                    algs=["RS256"],
                    check_claims=False,
                )
                return json.loads(parsed.claims)
            except Exception as exc:
                last_exc = exc

        # Retry once after reload (rotation)
        try:
            self.jwks_keys = self._load_jwks_keys()
            for key in self.jwks_keys:
                try:
                    parsed = jwcrypto_jwt.JWT(
                        jwt=token,
                        key=key,
                        algs=["RS256"],
                        check_claims=False,
                    )
                    return json.loads(parsed.claims)
                except Exception as exc:
                    last_exc = exc
        except Exception:
            pass

        raise AirflowException(f"JWT verification failed: {last_exc}")

    @staticmethod
    def _token_expired(token: str) -> bool:
        payload = OidcKeycloakAuthManager._decode_payload_no_verify(token)
        exp = payload.get("exp")
        if exp is None:
            return True
        return int(exp) < int(time.time())

    # -------------------------------------------------------------------------
    # Role extraction (STRICT: comma split, key = split[1])
    # -------------------------------------------------------------------------

    @staticmethod
    def _is_adminish_group(group_name: str) -> bool:
        g = (group_name or "").strip().lower()
        return g.endswith("_admin") or g.endswith("_superadmin") or g.endswith("_super_admin")

    def _extract_roles_from_token(self, token: str) -> Set[str]:
        claims = self._verify_and_decode_jwt(token)

        groups = claims.get("groups", []) or []
        if isinstance(groups, str):
            groups = [groups]

        roles: Set[str] = set()
        for entry in groups:
            parts = [p.strip() for p in str(entry).split(",") if p.strip()]
            if not parts:
                continue
            group_name = parts[1] if len(parts) >= 2 else parts[0]

            mapped = self.roles_mapping.get(group_name)
            if mapped:
                roles.update(mapped)
            elif self._is_adminish_group(group_name):
                roles.add("ADMIN")

        return roles

    # -------------------------------------------------------------------------
    # RBAC evaluation
    # -------------------------------------------------------------------------

    def _role_allows(self, roles: Set[str], resource_type: KeycloakResource, method: str) -> bool:
        res = resource_type.value
        m = (method or "").upper()

        for r in roles:
            matrix = self.permissions_matrix.get(r)
            if not matrix:
                continue
            if "*" in matrix:
                return True
            allowed = matrix.get(res)
            if allowed and ("*" in allowed or m in allowed):
                return True
        return False

    # -------------------------------------------------------------------------
    # Session cleanup (never raise)
    # -------------------------------------------------------------------------

    def _safe_clear_session(self) -> None:
        try:
            from flask import has_request_context, session

            if not has_request_context():
                return
            # Conservative clear: remove common keys; keep others if needed
            for k in ("_token", "token", "access_token", "refresh_token", "id_token", "userinfo"):
                session.pop(k, None)
            session.modified = True
        except Exception as exc:
            log.debug("Session cleanup failed (ignored): %s", exc)

    # -------------------------------------------------------------------------
    # PRE-AUTH: allow UI to load without 401/403 flash
    # -------------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        """
        Must NEVER raise.
        PRE-AUTH returns True so UI can load with VIEWER authorization.
        Keycloak will still be triggered when protected actions/routes are hit.
        """
        try:
            user = super().get_user()
            if not user:
                return True  # PRE-AUTH VIEWER
            token = getattr(user, "access_token", None)
            if not token:
                return True
            # If expired => not logged in => forces OAuth on next protected flow
            return not self._token_expired(token)
        except Exception as exc:
            log.warning("is_logged_in error (treated as PRE-AUTH): %s", exc)
            return True

    # -------------------------------------------------------------------------
    # User retrieval & refresh (safe; never raise)
    # -------------------------------------------------------------------------

    def get_user(self) -> Optional[KeycloakAuthManagerUser]:
        """
        Only place where refresh is attempted.
        NEVER raise; return None on failure to force OAuth re-auth.
        """
        try:
            user = super().get_user()
            if not user:
                return None

            token = getattr(user, "access_token", None)
            if not token:
                return user

            try:
                if not self._token_expired(token):
                    return user
            except Exception as exc:
                log.warning("Token exp check failed; forcing re-auth: %s", exc)
                self._safe_clear_session()
                return None

            refreshed = self.refresh_user(user=user)
            if refreshed:
                return refreshed

            self._safe_clear_session()
            return None

        except Exception as exc:
            log.error("get_user unexpected error (forcing logout): %s", exc)
            self._safe_clear_session()
            return None

    def refresh_user(self, user: KeycloakAuthManagerUser) -> Optional[KeycloakAuthManagerUser]:
        """
        Catch Keycloak refresh errors to prevent 500.
        """
        try:
            return super().refresh_user(user=user)
        except Exception as exc:
            msg = str(exc)
            if "invalid_grant" in msg or "Token is not active" in msg or "expired" in msg.lower():
                log.warning("Refresh token invalid/expired: %s", msg)
            else:
                log.error("Refresh token unexpected error: %s", msg)
            # Do not clear session here unconditionally; get_user will do it.
            return None

    # -------------------------------------------------------------------------
    # Authorization (never raise; never refresh; never mutate user)
    # -------------------------------------------------------------------------

    def _is_authorized(
        self,
        *,
        method,
        resource_type: KeycloakResource,
        user: KeycloakAuthManagerUser,
        resource_id=None,
        attributes=None,
    ) -> bool:
        try:
            # PRE-AUTH => VIEWER
            if not user:
                roles = {self.pre_auth_role}
            else:
                token = getattr(user, "access_token", None)
                if not token:
                    return False
                # If expired here => deny; get_user()/is_logged_in drives re-auth
                try:
                    if self._token_expired(token):
                        return False
                except Exception:
                    return False

                try:
                    roles = self._extract_roles_from_token(token)
                except Exception as exc:
                    log.warning("Role extraction failed (deny): %s", exc)
                    return False

                if not roles:
                    roles = {self.default_role}

            method_str = method.value if hasattr(method, "value") else str(method)

            # Preserve your collection convention: GET with no resource_id => LIST
            if method_str.upper() == "GET" and resource_id is None:
                method_str = "LIST"

            return self._role_allows(roles, resource_type, method_str)

        except Exception as exc:
            # Absolute last line of defense: never 500
            log.error("_is_authorized unexpected error (deny): %s", exc)
            return False

    def _is_batch_authorized(self, *, permissions, user):
        """
        Must return a set; never raise.
        """
        try:
            roles: Set[str] = {self.pre_auth_role}

            if user and getattr(user, "access_token", None):
                token = user.access_token
                try:
                    if not self._token_expired(token):
                        roles = self._extract_roles_from_token(token) or {self.default_role}
                    else:
                        return set()
                except Exception as exc:
                    log.warning("Batch auth token/roles failure: %s", exc)
                    return set()

            allowed = set()
            for method, res in permissions:
                method_str = method.value if hasattr(method, "value") else str(method)
                resource = res if isinstance(res, KeycloakResource) else KeycloakResource(res)
                if self._role_allows(roles, resource, method_str):
                    allowed.add((method, res))
            return allowed

        except Exception as exc:
            log.error("_is_batch_authorized unexpected error: %s", exc)
            return set()

    # -------------------------------------------------------------------------
    # UMA disabled explicitly
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")