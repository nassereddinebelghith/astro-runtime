import requests
from keycloak.exceptions import KeycloakPostError
from airflow.providers.keycloak.auth_manager.keycloak_auth_manager import (
    KeycloakAuthManager,
)


class OdysseyKeycloakAuthManager(KeycloakAuthManager):

    def refresh_tokens(self, refresh_token: str) -> dict[str, str]:
        """
        Call Keycloak token endpoint to refresh tokens.

        Returns:
            {
                "access_token": "...",
                "refresh_token": "..."
            }
        """

        client = self.get_keycloak_client()

        token_url = (
            f"{client.server_url}/realms/{client.realm_name}"
            "/protocol/openid-connect/token"
        )

        data = {
            "grant_type": "refresh_token",
            "client_id": client.client_id,
            "refresh_token": refresh_token,
        }

        if client.client_secret_key:
            data["client_secret"] = client.client_secret_key

        response = requests.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if response.status_code != 200:
            raise KeycloakPostError(
                error_message=response.text,
                response_code=response.status_code,
            )

        payload = response.json()

        return {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
        }