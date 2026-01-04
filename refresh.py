def refresh_user(
    self,
    *,
    user: KeycloakAuthManagerUser
) -> KeycloakAuthManagerUser | None:
    """
    Refresh tokens when access token OR refresh token is expired.
    If refresh fails, force logout (return None).
    """

    # 1. Check expiration of BOTH tokens
    access_expired = self._token_expired(user.access_token)
    refresh_expired = self._token_expired(user.refresh_token)

    if not access_expired and not refresh_expired:
        # Nothing to do
        return user

    log.info(
        "Token expired (access=%s, refresh=%s) -> attempting refresh",
        access_expired,
        refresh_expired,
    )

    # 2. Try refresh using refresh_token
    try:
        client = self.get_keycloak_client()
        tokens = client.refresh_token(user.refresh_token)
    except KeycloakPostError as exc:
        msg = str(exc).lower()

        # Refresh token invalid / expired -> force login
        if "invalid_grant" in msg or "token is not active" in msg:
            log.info("Refresh token expired or invalid -> forcing re-login")
        else:
            log.warning("Unexpected Keycloak refresh error", exc_info=exc)

        return None

    # 3. Defensive validation
    if not tokens or "access_token" not in tokens or "refresh_token" not in tokens:
        log.warning("Keycloak returned invalid refresh response -> forcing re-login")
        return None

    # 4. Update user tokens
    user.access_token = tokens["access_token"]
    user.refresh_token = tokens["refresh_token"]

    log.info("Tokens successfully refreshed")

    return user