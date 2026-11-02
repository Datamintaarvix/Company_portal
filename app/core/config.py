"""Application configuration using Pydantic Settings.

Reads configuration from environment variables and an optional .env file.
Provides strongly-typed settings across development, testing, and production.
"""

import os
from functools import lru_cache
from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    """Central configuration for Company Portal."""

    # Project metadata
    PROJECT_NAME: str = "Company Employee Portal"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server settings
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Security & Session settings
    SECRET_KEY: str = "dev-insecure-secret-key-replace-in-production-must-be-long-and-random"
    SESSION_COOKIE_NAME: str = "session_id"
    CSRF_COOKIE_NAME: str = "csrf_token"
    SESSION_EXPIRE_MINUTES: int = 120
    COOKIE_SECURE: Optional[bool] = None
    COOKIE_SAMESITE: str = "lax"
    RATE_LIMIT_LOGIN_MAX_ATTEMPTS: int = 5
    RATE_LIMIT_LOGIN_WINDOW_SECONDS: int = 300

    # Company & Localization settings
    COMPANY_TIMEZONE: str = "Asia/Kolkata"

    # PostgreSQL Database settings
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "company_portal"
    DATABASE_URL: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    @model_validator(mode="after")
    def validate_environment_configuration(self) -> "Settings":
        """Enforce strict isolation rules across environments."""
        env = self.ENVIRONMENT.lower()

        if env == "staging":
            # 1. Database isolation safeguard: Staging must NEVER target dev DB
            db_target = self.POSTGRES_DB
            if self.DATABASE_URL:
                try:
                    db_target = make_url(self.DATABASE_URL).database
                except Exception:
                    raise ValueError(
                        "FATAL CONFIGURATION ERROR: Invalid DATABASE_URL format in staging."
                    ) from None

            if db_target == "company_portal":
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: Staging environment cannot use the development "
                    "database 'company_portal'. Please configure POSTGRES_DB=company_portal_staging "
                    "or specify an isolated staging DATABASE_URL."
                )

            # 2. Secret key safeguard: Development default secret key rejected
            if "dev-insecure" in self.SECRET_KEY or self.SECRET_KEY.startswith("dev-"):
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: Staging environment cannot use the default development "
                    "SECRET_KEY. Please provide a secure random SECRET_KEY in the staging configuration."
                )

        elif env == "production":
            # 1. Production SECRET_KEY validation:
            # - Require an explicitly configured secret
            # - Reject existing dev default
            # - Reject literal placeholder from example file
            # - Require at least 32 characters
            # - Never expose secret values in errors or logs
            secret = self.SECRET_KEY or ""
            if (
                not secret
                or "dev-insecure" in secret
                or secret.startswith("dev-")
                or secret == "generate_a_random_48_char_secret_key_here"
                or len(secret) < 32
            ):
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: Production requires an explicitly configured, "
                    "cryptographically secure SECRET_KEY of at least 32 characters. Development defaults "
                    "and example placeholders are strictly prohibited."
                )

            # 2. Production POSTGRES_PASSWORD validation:
            # - Require explicitly configured password
            # - Reject empty, 'postgres', and example placeholder
            # - Require at least 32 characters
            # - Never expose password in errors or logs
            effective_password = self.POSTGRES_PASSWORD or ""
            if self.DATABASE_URL:
                try:
                    parsed_url = make_url(self.DATABASE_URL)
                    if parsed_url.password:
                        effective_password = parsed_url.password
                except Exception:
                    raise ValueError(
                        "FATAL CONFIGURATION ERROR: Invalid DATABASE_URL format in production."
                    ) from None

            if (
                not effective_password
                or effective_password == "postgres"
                or effective_password == "generate_a_strong_database_password_here"
                or len(effective_password) < 32
            ):
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: Production requires an explicitly configured, "
                    "strong database password of at least 32 characters. Default 'postgres' "
                    "and example placeholders are strictly prohibited."
                )

            # 3. Production database isolation:
            # - Require effective database name to equal exactly 'company_portal_prod'
            # - Parse DATABASE_URL using SQLAlchemy make_url
            # - Reject dev ('company_portal'), staging ('company_portal_staging'), and unexpected names
            if self.DATABASE_URL:
                try:
                    parsed_url = make_url(self.DATABASE_URL)
                    effective_db = parsed_url.database
                except Exception:
                    raise ValueError(
                        "FATAL CONFIGURATION ERROR: Invalid DATABASE_URL format in production."
                    ) from None
            else:
                effective_db = self.POSTGRES_DB

            if effective_db != "company_portal_prod":
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: Production database name must be exactly 'company_portal_prod'. "
                    "Development ('company_portal'), staging ('company_portal_staging'), and unexpected database "
                    "targets are strictly prohibited."
                )

            # 4. Debug mode safeguard
            if self.DEBUG:
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: DEBUG mode must be disabled (False) in production."
                )

        return self

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """Construct the SQLAlchemy database connection URI using psycopg dialect."""
        if self.DATABASE_URL:
            url = make_url(self.DATABASE_URL)
            if url.drivername in ("postgresql", "postgres"):
                url = url.set(drivername="postgresql+psycopg")
            return url.render_as_string(hide_password=False)

        url = URL.create(
            drivername="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )
        return url.render_as_string(hide_password=False)

    @property
    def is_production(self) -> bool:
        """Helper to determine whether the app is running in production."""
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_cookie_secure(self) -> bool:
        """Determine whether cookies must use the Secure flag.

        Defaults to True in production and False in development/testing,
        unless explicitly overridden via COOKIE_SECURE.
        """
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.is_production


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of the application settings."""
    env_file = os.getenv("ENV_FILE", ".env")
    return Settings(_env_file=env_file)
