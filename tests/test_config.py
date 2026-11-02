"""Isolated unit tests for application configuration and environment safeguards.

Tests Pydantic validation rules, production security constraints, database isolation,
and SQLAlchemy connection string construction without connecting to any database.
"""

import pytest
from app.core.config import Settings


# Reusable strong fixtures for testing
VALID_PROD_SECRET = "a_very_secure_random_key_of_at_least_32_characters_12345"
VALID_PROD_PASSWORD = "a_very_strong_production_password_at_least_32_chars_long!"
VALID_PROD_DB = "company_portal_prod"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Ensure tests run with an isolated environment to prevent bleed-through."""
    for var in [
        "ENVIRONMENT", "SECRET_KEY", "POSTGRES_DB", "POSTGRES_USER",
        "POSTGRES_PASSWORD", "POSTGRES_SERVER", "POSTGRES_PORT",
        "DATABASE_URL", "DEBUG", "COOKIE_SECURE"
    ]:
        monkeypatch.delenv(var, raising=False)


def test_a_valid_production_configuration():
    """Verify that a properly configured production environment initializes cleanly."""
    settings = Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        SECRET_KEY=VALID_PROD_SECRET,
        POSTGRES_DB=VALID_PROD_DB,
        POSTGRES_USER="company_portal_prod_user",
        POSTGRES_PASSWORD=VALID_PROD_PASSWORD,
    )
    assert settings.is_production is True
    assert settings.is_cookie_secure is True
    assert settings.POSTGRES_DB == "company_portal_prod"
    assert "postgresql+psycopg://" in settings.SQLALCHEMY_DATABASE_URI
    assert "company_portal_prod" in settings.SQLALCHEMY_DATABASE_URI


def test_b_missing_production_secret_key():
    """Verify that an empty or missing SECRET_KEY is rejected in production."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY="",
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD=VALID_PROD_PASSWORD,
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "SECRET_KEY" in str(exc_info.value)


def test_c_development_default_secret_key():
    """Verify that the default development SECRET_KEY is rejected in production."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY="dev-insecure-secret-key-replace-in-production-must-be-long-and-random",
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD=VALID_PROD_PASSWORD,
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "SECRET_KEY" in str(exc_info.value)


def test_d_placeholder_secret_key():
    """Verify that example template placeholders and short keys are rejected in production."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY="generate_a_random_48_char_secret_key_here",
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD=VALID_PROD_PASSWORD,
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY="short_production_secret",
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD=VALID_PROD_PASSWORD,
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)


def test_e_missing_production_postgres_password():
    """Verify that an empty POSTGRES_PASSWORD is rejected in production."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD="",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "POSTGRES_PASSWORD" in str(exc_info.value) or "password" in str(exc_info.value)


def test_f_placeholder_or_weak_postgres_password():
    """Verify that default 'postgres', template placeholder, and short passwords are rejected."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD="postgres",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD="generate_a_strong_database_password_here",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            POSTGRES_DB=VALID_PROD_DB,
            POSTGRES_PASSWORD="short_pwd_12345",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)


def test_g_production_database_url_pointing_to_company_portal():
    """Verify that DATABASE_URL targeting development database 'company_portal' is rejected."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            DATABASE_URL=f"postgresql+psycopg://user:{VALID_PROD_PASSWORD}@localhost:5432/company_portal",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "company_portal_prod" in str(exc_info.value)


def test_h_production_database_url_pointing_to_company_portal_staging():
    """Verify that DATABASE_URL targeting staging database 'company_portal_staging' is rejected."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            DATABASE_URL=f"postgresql+psycopg://user:{VALID_PROD_PASSWORD}@localhost:5432/company_portal_staging",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "company_portal_prod" in str(exc_info.value)


def test_i_production_database_url_pointing_to_unexpected_database():
    """Verify that DATABASE_URL targeting an unexpected database name is rejected."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY=VALID_PROD_SECRET,
            DATABASE_URL=f"postgresql+psycopg://user:{VALID_PROD_PASSWORD}@localhost:5432/random_other_db",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "company_portal_prod" in str(exc_info.value)


def test_j_valid_production_database_url():
    """Verify that a valid DATABASE_URL targeting 'company_portal_prod' initializes and normalizes dialect."""
    raw_url = f"postgresql://prod_user:{VALID_PROD_PASSWORD}@db.internal:5432/company_portal_prod"
    settings = Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        SECRET_KEY=VALID_PROD_SECRET,
        DATABASE_URL=raw_url,
    )
    assert settings.is_production is True
    assert settings.SQLALCHEMY_DATABASE_URI.startswith("postgresql+psycopg://")
    assert "company_portal_prod" in settings.SQLALCHEMY_DATABASE_URI


def test_k_special_characters_in_database_credentials():
    """Verify that URL.create safely encodes special characters in username and password."""
    complex_password = "p@ss#word!$%^&*()_+=~`{}[]:;'<>,.?/123456789"
    settings = Settings(
        ENVIRONMENT="production",
        DEBUG=False,
        SECRET_KEY=VALID_PROD_SECRET,
        POSTGRES_DB=VALID_PROD_DB,
        POSTGRES_USER="user+test#1",
        POSTGRES_PASSWORD=complex_password,
    )
    uri = settings.SQLALCHEMY_DATABASE_URI
    assert "%40" in uri
    assert "%23" in uri
    assert "company_portal_prod" in uri


def test_l_existing_development_and_staging_configurations():
    """Verify that development and staging configurations continue to function with their defaults."""
    dev_settings = Settings(
        ENVIRONMENT="development",
    )
    assert dev_settings.is_production is False
    assert dev_settings.is_cookie_secure is False
    assert dev_settings.POSTGRES_DB == "company_portal"
    assert "company_portal" in dev_settings.SQLALCHEMY_DATABASE_URI

    staging_settings = Settings(
        ENVIRONMENT="staging",
        DEBUG=False,
        SECRET_KEY="staging_random_secret_key_configured_properly",
        POSTGRES_DB="company_portal_staging",
        POSTGRES_USER="company_portal_staging_user",
        POSTGRES_PASSWORD="staging_password_here",
    )
    assert staging_settings.is_production is False
    assert staging_settings.is_cookie_secure is False
    assert staging_settings.POSTGRES_DB == "company_portal_staging"

    with pytest.raises(ValueError) as exc_info:
        Settings(
            ENVIRONMENT="staging",
            SECRET_KEY="staging_random_secret_key_configured_properly",
            POSTGRES_DB="company_portal",
        )
    assert "FATAL CONFIGURATION ERROR" in str(exc_info.value)
    assert "company_portal_staging" in str(exc_info.value)
