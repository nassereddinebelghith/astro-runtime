from airflow.providers.keycloak.auth_manager.keycloak_auth_manager import (
    KeycloakAuthManager,
    KeycloakAuthManagerUser,
)
from keycloak.exceptions import KeycloakPostError
import logging
import time

log = logging.getLogger(__name__)


class OidcKeycloakAuthManager(KeycloakAuthManager):

    @staticmethod
    def _token_expired(token: str) -> bool:
        payload = OidcKeycloakAuthManager._decode_jwt_payload(token)
        exp = payload.get("exp")
        return exp is None or exp < int(time.time())

    def refresh_user(
        self,
        *,
        user: KeycloakAuthManagerUser,
    ) -> KeycloakAuthManagerUser | None:
        """
        Refresh user tokens if access OR refresh token is expired.
        Force re-login if refresh fails.
        """

        access_expired = self._token_expired(user.access_token)
        refresh_expired = self._token_expired(user.refresh_token)

        if not access_expired and not refresh_expired:
            return user

        try:
            client = self.get_keycloak_client()
            tokens = client.refresh_token(user.refresh_token)
        except KeycloakPostError as exc:
            msg = str(exc).lower()
            if "invalid_grant" in msg or "token is not active" in msg:
                log.info("Refresh token expired or invalid → forcing re-login")
            else:
                log.warning("Unexpected Keycloak refresh error", exc_info=exc)
            return None

        if not tokens or "access_token" not in tokens or "refresh_token" not in tokens:
            return None

        user.access_token = tokens["access_token"]
        user.refresh_token = tokens["refresh_token"]

        return user