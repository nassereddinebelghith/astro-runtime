def is_logged_in(self) -> bool:
    """
    Force Airflow to redirect to login when refresh token has expired.
    """
    user = self.get_user()

    # No user -> not logged in -> redirect to login
    if not user:
        return False

    # Session explicitly marked as invalid
    if has_request_context() and session.get("kc_refresh_failed"):
        return False

    return True