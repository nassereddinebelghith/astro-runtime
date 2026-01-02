from __future__ import annotations

import json
import logging
import os
import time
from base64 import urlsafe_b64decode
from typing import Dict, List, Optional, Set

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


# =============================================================================
# Helpers
# =============================================================================

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
    if v in {"pre_login", "pre-login", "public"}:
        return "PRE_LOGIN"
    return (role or "").strip().upper()


def _safe_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


# =============================================================================
# Auth Manager
# =============================================================================

class OidcKeycloakAuthManager(KeycloakAuthManager):
    """
    FINAL CONSOLIDATED – Airflow 3.x – OIDC Keycloak – NO UMA

    Constraints satisfied:
      1) Mapping stable and strict:
         - groups claim contains strings like "D-00000xxxxx,group_name"
         - group_name = split(',')[1]
         - roles_mapping[group_name] -> internal roles
         - fallback: *_admin and *_superadmin => ADMIN

      2) No Internal Server Error due to token refresh / token invalid / token expired:
         - NEVER raise in is_logged_in(), get_user(), refresh_user(),
           _is_authorized(), _is_batch_authorized().
         - refresh is attempted ONLY in get_user()
         - all refresh errors are caught (invalid_grant, token not active, etc.)
         - session is cleaned safely and None is returned to force OAuth

      3) PRE-LOGIN role to avoid initial 401/403 flash, BUT Keycloak MUST be triggered automatically:
         - Phase 1 (first request in a fresh browser session): return logged_in=True
           + authorize with PRE_LOGIN role (very minimal)
         - Phase 2 (immediately after): force OAuth by returning logged_in=False
           if no authenticated user exists (no click needed)
         - Implementation uses a per-session flag: session["__pre_login_seen__"]

    Optional knobs (env):
      - ROLES_MAPPING: JSON map { "<group_name>": ["ADMIN"|"OP"|"USER"|"VIEWER"] }
      - DEFAULT_ROLE: fallback role when authenticated but mapping yields empty (default VIEWER)
      - PRE_LOGIN_ROLE: role used before OAuth (default PRE_LOGIN)
      - FORCE_OAUTH_AFTER_PRELOGIN: "true"/"false" (default true)
      - PRELOGIN_MAX_AGE_SECONDS: max age to keep prelogin before forcing OAuth (default 5)
    """

    PRELOGIN_SEEN_KEY = "__pre_login_seen__"
    PRELOGIN_TS_KEY = "__pre_login_ts__"

    def __init__(self) -> None:
        super().__init__()

        # Roles used for controlled behavior
        self.pre_login_role: str = _normalize_role(os.getenv("PRE_LOGIN_ROLE", "PRE_LOGIN"))
        self.default_role: str = _normalize_role(os.getenv("DEFAULT_ROLE", "VIEWER"))

        self.force_oauth_after_prelogin: bool = (
            os.getenv("FORCE_OAUTH_AFTER_PRELOGIN", "true").strip().lower() == "true"
        )
        # Small window where we allow UI assets to load without a visible 401/403,
        # then we force OAuth on next request automatically.
        self.prelogin_max_age_seconds: int = _safe_int_env("PRELOGIN_MAX_AGE_SECONDS", 5)

        # Load ROLES_MAPPING (group_name -> set(roles))
        raw_mapping = os.getenv("ROLES_MAPPING", "{}")
        try:
            parsed = json.loads(raw_mapping)
            if not isinstance(parsed, dict):
                raise ValueError("ROLES_MAPPING must be a JSON object")
        except Exception as exc:
            raise AirflowException(f"Invalid ROLES_MAPPING JSON: {raw_mapping}") from exc

        self.roles_mapping: Dict[str, Set[str]] = {}
        for group_name, roles in parsed.items():
            if roles is None:
                continue
            if isinstance(roles, str):
                roles = [roles]
            self.roles_mapping[str(group_name)] = {_normalize_role(str(r)) for r in roles}

        # RBAC permissions matrix – complete baseline for Airflow 3 UI/API
        # ADMIN remains "*:*" exactly as you require.
        self.permissions_matrix: Dict[str, Dict[str, Set[str]]] = {
            "ADMIN": {"*": {"*"}},

            # PRE_LOGIN: minimal UI shell only (read-only / non-sensitive).
            # You can widen carefully if needed; keep it GET/LIST only.
            "PRE_LOGIN": {
                "VIEW": {"GET"},
                "MENU": {"GET"},
                # Allow read-only navigation; adjust if your UI needs it.
                "DAG": {"GET", "LIST"},
                "DAG_RUN": {"GET", "LIST"},
                "TASK_INSTANCE": {"GET", "LIST"},
                "TASK_LOG": {"GET", "LIST"},
                "IMPORT_ERROR": {"GET", "LIST"},
                "JOB": {"GET", "LIST"},
                "POOL": {"GET", "LIST"},
                "ASSET": {"GET", "LIST"},
                "ASSET_ALIAS": {"GET", "LIST"},
                "XCOM": {"GET", "LIST"},
            },

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

        # JWKS setup (soft-fail at init to avoid crashing the webserver at startup)
        server_url = conf.get(CONF_SECTION_NAME, CONF_SERVER_URL_KEY).rstrip("/")
        realm = conf.get(CONF_SECTION_NAME, CONF_REALM_KEY)
        self.jwks_url = f"{server_url}/realms/{realm}/protocol/openid-connect/certs"
        self.jwks_keys: List[jwk.JWK] = []
        try:
            self.jwks_keys = self._load_jwks_keys()
            log.info("Loaded %d JWKS keys from %s", len(self.jwks_keys), self.jwks_url)
        except Exception as exc:
            log.error("JWKS init load failed (will deny until available): %s", exc)
            self.jwks_keys = []

    # =============================================================================
    # JWKS / JWT
    # =============================================================================

    def _load_jwks_keys(self) -> List[jwk.JWK]:
        resp = requests.get(self.jwks_url, timeout=5)
        resp.raise_for_status()
        jwks = resp.json()
        keys: List[jwk.JWK] = []
        for k in jwks.get("keys", []) or []:
            try:
                keys.append(jwk.JWK(**k))
            except Exception as exc:
                log.warning("Skipping invalid JWKS key: %s", exc)
        if not keys:
            raise AirflowException("No valid JWKS keys returned by Keycloak")
        return keys

    @staticmethod
    def _decode_payload_no_verify(token: str) -> dict:
        token = (token or "").strip()
        if token.startswith("Bearer "):
            token = token[7:]
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(urlsafe_b64decode(padded))

    def _verify_and_decode_jwt(self, token: str) -> dict:
        token = (token or "").strip()
        if token.startswith("Bearer "):
            token = token[7:]

        # If no keys loaded, attempt reload once (rotation / startup ordering)
        if not self.jwks_keys:
            self.jwks_keys = self._load_jwks_keys()

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

        # Retry once after reload
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

        raise AirflowException(f"JWT verification failed: {last_exc}")

    @staticmethod
    def _token_expired(token: str) -> bool:
        payload = OidcKeycloakAuthManager._decode_payload_no_verify(token)
        exp = payload.get("exp")
        if exp is None:
            return True
        return int(exp) < int(time.time())

    # =============================================================================
    # Session helpers (never raise)
    # =============================================================================

    def _safe_session_get(self, key: str, default=None):
        try:
            from flask import has_request_context, session
            if not has_request_context():
                return default
            return session.get(key, default)
        except Exception:
            return default

    def _safe_session_set(self, key: str, value) -> None:
        try:
            from flask import has_request_context, session
            if not has_request_context():
                return
            session[key] = value
            session.modified = True
        except Exception:
            return

    def _safe_clear_session_tokens(self) -> None:
        """Best-effort cleanup of auth-related session keys (never raises)."""
        try:
            from flask import has_request_context, session
            if not has_request_context():
                return
            for k in ("_token", "token", "access_token", "refresh_token", "id_token", "userinfo"):
                session.pop(k, None)
            session.modified = True
        except Exception:
            pass

    def _mark_prelogin_seen(self) -> None:
        self._safe_session_set(self.PRELOGIN_SEEN_KEY, True)
        self._safe_session_set(self.PRELOGIN_TS_KEY, int(time.time()))

    def _prelogin_is_recent(self) -> bool:
        ts = self._safe_session_get(self.PRELOGIN_TS_KEY, None)
        if ts is None:
            return False
        try:
            return (int(time.time()) - int(ts)) <= int(self.prelogin_max_age_seconds)
        except Exception:
            return False

    # =============================================================================
    # Role extraction (strict contract)
    # =============================================================================

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

    # =============================================================================
    # RBAC evaluation
    # =============================================================================

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

    # =============================================================================
    # Core lifecycle: login + refresh (NEVER raise)
    # =============================================================================

    def is_logged_in(self) -> bool:
        """
        Two-phase auto OAuth without user click:

        - First request (no user, prelogin not seen): return True (PRE_LOGIN role applies)
          to avoid 401/403 flash while UI assets bootstrap.
        - Immediately after (no user, prelogin already seen): return False to trigger OAuth redirect.

        If force_oauth_after_prelogin is disabled:
          - always return True pre-auth (viewer/public mode), OAuth triggers only on protected actions.
        """
        try:
            user = super().get_user()
            if user:
                token = getattr(user, "access_token", None)
                if not token:
                    return True
                try:
                    return not self._token_expired(token)
                except Exception:
                    # Fail closed: force OAuth
                    return False

            # No authenticated user
            pre_seen = bool(self._safe_session_get(self.PRELOGIN_SEEN_KEY, False))
            if not pre_seen:
                # Phase 1: allow UI to load minimally
                self._mark_prelogin_seen()
                return True

            # Phase 2: force OAuth (auto) unless explicitly disabled
            if self.force_oauth_after_prelogin:
                # Small guard: don't loop too aggressively; allow a tiny grace window
                # for static assets to complete on the first burst.
                if self._prelogin_is_recent():
                    return True
                return False

            return True

        except Exception as exc:
            # Never 500: choose a safe default.
            log.warning("is_logged_in error (safe fallback): %s", exc)
            # Conservative: force OAuth (more secure) after prelogin has been seen.
            pre_seen = bool(self._safe_session_get(self.PRELOGIN_SEEN_KEY, False))
            return False if pre_seen else True

    def get_user(self) -> Optional[KeycloakAuthManagerUser]:
        """
        Only place where refresh is attempted.
        Never raises. Returns None to force OAuth when needed.
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
                self._safe_clear_session_tokens()
                return None

            refreshed = self.refresh_user(user=user)
            if refreshed:
                return refreshed

            self._safe_clear_session_tokens()
            return None

        except Exception as exc:
            log.error("get_user unexpected error; forcing logout: %s", exc)
            self._safe_clear_session_tokens()
            return None

    def refresh_user(self, user: KeycloakAuthManagerUser) -> Optional[KeycloakAuthManagerUser]:
        """
        Catch refresh errors to prevent 500.
        """
        try:
            return super().refresh_user(user=user)
        except Exception as exc:
            msg = str(exc)
            if "invalid_grant" in msg or "Token is not active" in msg or "expired" in msg.lower():
                log.warning("Refresh token invalid/expired: %s", msg)
            else:
                log.error("Refresh token unexpected error: %s", msg)
            return None

    # =============================================================================
    # Authorization (NEVER raise; never refresh; stable)
    # =============================================================================

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
        - PRE-AUTH: authorize with PRE_LOGIN role (minimal) to avoid 401/403 flash.
        - AUTH: extract roles from token; if empty -> DEFAULT_ROLE (safe).
        - Never refresh here; never raise.
        """
        try:
            # Determine roles
            if not user:
                roles = {self.pre_login_role}
            else:
                token = getattr(user, "access_token", None)
                if not token:
                    return False
                try:
                    if self._token_expired(token):
                        return False
                except Exception:
                    return False

                try:
                    roles = self._extract_roles_from_token(token)
                except Exception as exc:
                    log.warning("Role extraction failed; deny: %s", exc)
                    return False

                if not roles:
                    roles = {self.default_role}

            # Normalize method (preserve your GET collection => LIST convention)
            method_str = method.value if hasattr(method, "value") else str(method)
            if method_str.upper() == "GET" and resource_id is None:
                method_str = "LIST"

            return self._role_allows(roles, resource_type, method_str)

        except Exception as exc:
            log.error("_is_authorized unexpected error; deny: %s", exc)
            return False

    def _is_batch_authorized(self, *, permissions, user):
        """
        Must return a set. Never raises.
        """
        try:
            roles: Set[str] = {self.pre_login_role}

            if user and getattr(user, "access_token", None):
                token = user.access_token
                try:
                    if self._token_expired(token):
                        return set()
                except Exception:
                    return set()

                try:
                    roles = self._extract_roles_from_token(token) or {self.default_role}
                except Exception as exc:
                    log.warning("Batch role extraction failed; deny: %s", exc)
                    return set()

            allowed = set()
            for method, res in permissions:
                method_str = method.value if hasattr(method, "value") else str(method)
                resource = res if isinstance(res, KeycloakResource) else KeycloakResource(res)
                if self._role_allows(roles, resource, method_str):
                    allowed.add((method, res))
            return allowed

        except Exception as exc:
            log.error("_is_batch_authorized unexpected error; deny: %s", exc)
            return set()

    # =============================================================================
    # UMA disabled explicitly
    # =============================================================================

    @staticmethod
    def _get_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")

    @staticmethod
    def _get_batch_payload(*args, **kwargs):
        raise AirflowException("UMA is disabled in this AuthManager.")