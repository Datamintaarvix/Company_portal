"""Tests for root and health check endpoints."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root_endpoint():
    """Test that the root endpoint returns a 200 OK and expected welcome payload."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "docs_url" in data
    assert data["docs_url"] == "/docs"
    assert data["health_url"] == "/health"
    assert "version" in data


def test_health_check_endpoint():
    """Test that the health endpoint reports status 'healthy'."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "company-portal"
    assert "environment" in data
    assert "version" in data


def test_docs_endpoints():
    """Test that the OpenAPI spec and interactive documentation endpoints are accessible."""
    openapi_resp = client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    assert "openapi" in openapi_resp.json()

    docs_resp = client.get("/docs")
    assert docs_resp.status_code == 200
    assert "text/html" in docs_resp.headers["content-type"]


def test_database_health_check_endpoint():
    """Test that the database health check endpoint reports status 'healthy' when DB is reachable."""
    response = client.get("/health/db")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["database_name"] == "company_portal"
    assert "environment" in data


def test_not_found_endpoint():
    """Test that requesting an unknown path returns a 404 status."""
    response = client.get("/api/non-existent-route")
    assert response.status_code == 404
