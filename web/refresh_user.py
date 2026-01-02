def refresh_user(self, *, user):
    """
    Override critique pour éviter les 500 Internal Server Error
    quand le refresh_token Keycloak est expiré ou invalide.

    Règle Airflow:
    - refresh_user() DOIT retourner un user ou None
    - refresh_user() NE DOIT JAMAIS lever d’exception
    """

    try:
        # Appel du comportement standard du provider Keycloak
        return super().refresh_user(user=user)

    except KeycloakPostError as exc:
        # Cas NORMAL: refresh_token expiré / invalid_grant
        log.warning(
            "Keycloak refresh failed (invalid_grant). Forcing re-login: %s",
            exc,
        )

        # Nettoyage défensif pour éviter toute boucle
        try:
            user.access_token = ""
            user.refresh_token = ""
        except Exception:
            pass

        # IMPORTANT: None = session expirée => Airflow relance l’auth
        return None

    except Exception:
        # Sécurité absolue: aucune exception ne doit sortir d’ici
        log.exception(
            "Unexpected error during refresh_user. Forcing re-login."
        )

        try:
            user.access_token = ""
            user.refresh_token = ""
        except Exception:
            pass

        return None