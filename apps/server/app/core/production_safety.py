"""Validate production secrets before startup mutates storage or bootstrap data."""

from cryptography.fernet import Fernet


def validate_production_settings(settings) -> None:
    if not settings.is_production:
        return
    failures = []
    if len(settings.jwt_secret) < 32 or settings.jwt_secret == "change-me-in-development":
        failures.append("JWT_SECRET must be an independent random secret of at least 32 characters")
    try:
        Fernet(settings.provider_encryption_key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        failures.append("PROVIDER_ENCRYPTION_KEY must be a valid Fernet key")
    if settings.provider_encryption_key == settings.jwt_secret:
        failures.append("PROVIDER_ENCRYPTION_KEY must differ from JWT_SECRET")
    if (len(settings.bootstrap_admin_password) < 12
            or settings.bootstrap_admin_password == "change-me-on-first-login"):
        failures.append("BOOTSTRAP_ADMIN_PASSWORD must be explicitly configured with at least 12 characters")
    if failures:
        # Only configuration names and requirements, never supplied secret values.
        raise RuntimeError("Unsafe production configuration: " + "; ".join(failures))
