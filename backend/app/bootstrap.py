"""Startup tasks that must run once the database is ready."""

import structlog

logger = structlog.get_logger()


def bootstrap_admin(km, crypto, settings) -> str:
    """Close the first-run window on public deployments.

    By default the first visitor to /dashboard/login chooses the admin password.
    Setting ADMIN_BOOTSTRAP_PASSWORD makes the operator the first one to do so:
    the hash is stored at startup only if no admin exists yet, so changing or
    removing the variable later never overwrites an existing password.

    Returns "existing" | "bootstrapped" | "unclaimed".
    """
    if km.get_admin_password_hash() is not None:
        return "existing"
    password = (getattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "") or "").strip()
    if password:
        if len(password) < 12:
            raise RuntimeError("ADMIN_BOOTSTRAP_PASSWORD must be at least 12 characters")
        if not km.save_admin_password_hash(crypto.hash_password(password)):
            raise RuntimeError("Could not persist bootstrap administrator")
        logger.info("admin_bootstrapped_from_env")
        return "bootstrapped"
    if not settings.DEBUG:
        raise RuntimeError("Set ADMIN_BOOTSTRAP_PASSWORD (12+ characters) before production startup")
    return "unclaimed"
