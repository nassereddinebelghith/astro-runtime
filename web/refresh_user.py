def refresh_user(self, *, user: KeycloakAuthManagerUser) -> KeycloakAuthManagerUser | None:
    """
    Refresh access token if expired.
    If Keycloak says refresh token is invalid (invalid_grant), we must force a re-login
    with an explicit 401 instead of letting it explode into 500 or degrading into 403.
    """
    try:
        # If token still valid -> nothing to do
        if not self._token_expired(user.access_token):
            return user

        client = self.get_keycloak_client()
        tokens = client.refresh_token(user.refresh_token)

        user.refresh_token = tokens["refresh_token"]
        user.access_token = tokens["access_token"]
        return user

    except KeycloakPostError as exc:
        # Keycloak returns: 400 {"error":"invalid_grant","error_description":"Token is not active"}
        msg = str(exc)

        if getattr(exc, "response_code", None) == 400 and "invalid_grant" in msg:
            # IMPORTANT: do not return None here (often becomes 403 without re-login)
            # and do not let exception bubble (becomes 500).
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Session expired. Please re-authenticate.",
            )

        # Any other Keycloak error: keep it visible (real infra/config problem)
        raise