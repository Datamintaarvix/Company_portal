"""Isolated tests for production readiness, root endpoint, and mocked health endpoints.

Tests root endpoint docs_url behavior across environments and verifies /health and /health/db
without establishing any real database connection.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import get_settings


@pytest.fixture
def client():
    """Create a TestClient instance for testing."""
    return TestClient(app)


def test_root_endpoint_development_and_staging(client, monkeypatch):
    """Test that RootResponse.docs_url returns '/docs' in development and staging environments."""
    settings = get_settings()

    # Test development mode
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    res_dev = client.get("/")
    assert res_dev.status_code == 200
    data_dev = res_dev.json()
    assert data_dev["docs_url"] == "/docs"
    assert data_dev["health_url"] == "/health"
    assert "version" in data_dev

    # Test staging mode
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    res_stg = client.get("/")
    assert res_stg.status_code == 200
    data_stg = res_stg.json()
    assert data_stg["docs_url"] == "/docs"
    assert data_stg["health_url"] == "/health"


def test_root_endpoint_production(client, monkeypatch):
    """Test that RootResponse.docs_url returns None in production environment."""
    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["docs_url"] is None
    assert data["health_url"] == "/health"
    assert "version" in data


def test_health_liveness_endpoint(client):
    """Test that /health returns HTTP 200 liveness independent of database connectivity."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "company-portal"


def test_health_db_endpoint_healthy_mocked(client):
    """Test that /health/db returns HTTP 200 when database connectivity is verified."""
    with patch("app.main.check_db_connection", return_value=True):
        response = client.get("/health/db")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"


def test_health_db_endpoint_unhealthy_mocked(client):
    """Test that /health/db returns HTTP 503 when database is unavailable."""
    with patch("app.main.check_db_connection", return_value=False):
        response = client.get("/health/db")
        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["status"] == "unhealthy"
        assert data["detail"]["database"] == "disconnected"
        assert "error" in data["detail"]
