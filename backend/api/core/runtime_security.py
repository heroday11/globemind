from __future__ import annotations

from api.core.environment import raw_setting, string_setting

PRODUCTION_ENVIRONMENTS = {"prod", "production"}
INSECURE_JWT_SECRETS = {"", "your-secret-key-change-in-production", "change-me"}
INSECURE_SEED_PASSWORDS = {"1232200", "admin", "password", "change-me"}


def is_production() -> bool:
    return string_setting("APP_ENV", "development").lower() in PRODUCTION_ENVIRONMENTS


def validate_runtime_security() -> None:
    """Fail startup when production would silently use development credentials."""
    if not is_production():
        return

    errors: list[str] = []
    jwt_secret = string_setting("JWT_SECRET_KEY")
    if jwt_secret in INSECURE_JWT_SECRETS or len(jwt_secret) < 32:
        errors.append("JWT_SECRET_KEY must be a non-default value of at least 32 characters")
    seed_password = raw_setting("SEED_DEFAULT_USER_PASSWORD")
    if seed_password and (
        seed_password.strip().lower() in INSECURE_SEED_PASSWORDS or len(seed_password) < 12
    ):
        errors.append(
            "SEED_DEFAULT_USER_PASSWORD must be empty or a non-default value of at least 12 characters"
        )
    cors_origins = string_setting("CORS_ORIGINS")
    if "*" in {item.strip() for item in cors_origins.split(",")}:
        errors.append("CORS_ORIGINS must not contain '*' in production")
    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))
