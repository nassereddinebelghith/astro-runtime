import logging
from keycloak.exceptions import KeycloakPostError
from fastapi import HTTPException, status

log = logging.getLogger(__name__)

class OidcKeycloakAuthManager(KeycloakAuthManager):

    def refresh_user(self, user):
        try:
            return super().refresh_user(user=user)

        except KeycloakPostError as exc:
            # Keycloak refuses refresh: refresh token no longer active
            if exc.response_code == 400 and b"invalid_grant" in exc.response_body:
                log.warning(
                    "Keycloak refresh failed (invalid_grant). Forcing re-login."
                )
                # IMPORTANT: raise 401 so Airflow triggers auth flow instead of RBAC 403
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session expired. Please re-authenticate.",
                )

            # Other KeycloakPostError: let it bubble (or handle specifically if you want)
            raise

        except Exception:
            # Conservative: avoid 500 loops
            log.exception("Unexpected error during refresh_user; forcing re-login.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session error. Please re-authenticate.",
            )