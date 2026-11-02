"""Focused tests for API documentation route exposure across environments.

Validates that /docs, /redoc, and /openapi.json are disabled (404) in production,
and enabled (200) in development and staging environments. Also verifies that
the root endpoint ('/') advertises docs_url: null in production and '/docs'
in development and staging.

Constructs fresh FastAPI application instances for each environment under test
with the configuration set before application initialization.
"""

from contextlib import contextmanager
import importlib
import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient


@contextmanager
def app_in_environment(env_vars: dict):
    """Context manager that constructs a fresh FastAPI application in a given environment.

    Sets environment variables prior to application construction, clears the
    cached Settings instance, mocks database connectivity checks to prevent
    any real database operations during startup, and reloads app.main.

    Upon exit, restores the original environment and reloads app.main to avoid
    state leakage into other test modules.
    """
    orig_env = os.environ.copy()
    try:
        os.environ.update(env_vars)
        import app.core.config
        app.core.config.get_settings.cache_clear()
        import app.main
        with patch("app.main.check_db_connection", return_value=True):
            importlib.reload(app.main)
            yield app.main.app
    finally:
        os.environ.clear()
        os.environ.update(orig_env)
        import app.core.config
        app.core.config.get_settings.cache_clear()
        import app.main
        with patch("app.main.check_db_connection", return_value=True):
            importlib.reload(app.main)


def test_production_documentation_routes_disabled():
    """Verify that in production, /docs, /redoc, and /openapi.json return 404,
    and root ('/') returns docs_url: null."""
    prod_env = {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "SECRET_KEY": "p" * 32,
        "POSTGRES_PASSWORD": "p" * 32,
        "POSTGRES_DB": "company_portal_prod",
    }

    with app_in_environment(prod_env) as app:
        client = TestClient(app)

        # Check documentation routes are disabled (404)
        docs_res = client.get("/docs")
        assert docs_res.status_code == 404, f"Expected 404 for /docs, got {docs_res.status_code}"

        redoc_res = client.get("/redoc")
        assert redoc_res.status_code == 404, f"Expected 404 for /redoc, got {redoc_res.status_code}"

        openapi_res = client.get("/openapi.json")
        assert openapi_res.status_code == 404, f"Expected 404 for /openapi.json, got {openapi_res.status_code}"

        # Check root endpoint advertises docs_url: null
        root_res = client.get("/")
        assert root_res.status_code == 200
        root_data = root_res.json()
        assert root_data["docs_url"] is None
        assert root_data["health_url"] == "/health"


def test_development_documentation_routes_enabled():
    """Verify that in development, /docs, /redoc, and /openapi.json return 200,
    and root ('/') advertises docs_url: '/docs'."""
    dev_env = {
        "ENVIRONMENT": "development",
        "DEBUG": "true",
    }

    with app_in_environment(dev_env) as app:
        client = TestClient(app)

        # Check documentation routes are enabled (200)
        docs_res = client.get("/docs")
        assert docs_res.status_code == 200
        assert "text/html" in docs_res.headers.get("content-type", "")

        redoc_res = client.get("/redoc")
        assert redoc_res.status_code == 200
        assert "text/html" in redoc_res.headers.get("content-type", "")

        openapi_res = client.get("/openapi.json")
        assert openapi_res.status_code == 200
        assert "openapi" in openapi_res.json()

        # Check root endpoint advertises docs_url: '/docs'
        root_res = client.get("/")
        assert root_res.status_code == 200
        root_data = root_res.json()
        assert root_data["docs_url"] == "/docs"
        assert root_data["health_url"] == "/health"


def test_staging_documentation_routes_enabled():
    """Verify that in staging, /docs, /redoc, and /openapi.json return 200,
    and root ('/') advertises docs_url: '/docs'."""
    staging_env = {
        "ENVIRONMENT": "staging",
        "DEBUG": "true",
        "SECRET_KEY": "s" * 32,
        "POSTGRES_DB": "company_portal_staging",
    }

    with app_in_environment(staging_env) as app:
        client = TestClient(app)

        # Check documentation routes are enabled (200)
        docs_res = client.get("/docs")
        assert docs_res.status_code == 200
        assert "text/html" in docs_res.headers.get("content-type", "")

        redoc_res = client.get("/redoc")
        assert redoc_res.status_code == 200
        assert "text/html" in redoc_res.headers.get("content-type", "")

        openapi_res = client.get("/openapi.json")
        assert openapi_res.status_code == 200
        assert "openapi" in openapi_res.json()

        # Check root endpoint advertises docs_url: '/docs'
        root_res = client.get("/")
        assert root_res.status_code == 200
        root_data = root_res.json()
        assert root_data["docs_url"] == "/docs"
        assert root_data["health_url"] == "/health"


def test_case_insensitive_production_documentation_routes_disabled():
    """Verify that case-insensitive production settings (e.g., 'PRODUCTION', 'Production')
    also disable all documentation routes and set docs_url to null."""
    for env_val in ["PRODUCTION", "Production"]:
        prod_env = {
            "ENVIRONMENT": env_val,
            "DEBUG": "false",
            "SECRET_KEY": "p" * 32,
            "POSTGRES_PASSWORD": "p" * 32,
            "POSTGRES_DB": "company_portal_prod",
        }

        with app_in_environment(prod_env) as app:
            client = TestClient(app)
            assert client.get("/docs").status_code == 404
            assert client.get("/redoc").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/").json()["docs_url"] is None
