class CustomKeycloakAuthManager(KeycloakAuthManager):

    async def refresh_user(self, user: User | None) -> User | None:
        if not user or not user.refresh_token:
            return None

        try:
            # Laisse le provider officiel tenter le refresh
            return await super().refresh_user(user)

        except KeycloakPostError as e:
            # CAS NORMAL : refresh_token expiré ou invalide
            if e.response_code == 400 and "invalid_grant" in str(e):
                logging.warning(
                    "Keycloak refresh failed (invalid_grant). "
                    "Clearing Airflow session and forcing re-login."
                )

                # 🔥 POINT CLÉ : on détruit la session Airflow
                clear_session()

                # IMPORTANT :
                # - on ne lève PAS d’exception
                # - on ne retourne PAS de user partiel
                # - Airflow va considérer l’utilisateur comme non authentifié
                return None

            # Autre erreur Keycloak → on propage
            raise

        except Exception:
            logging.exception("Unexpected error during refresh_user")
            clear_session()
            return None