"""Tests for the NoteTutor AI health endpoint."""

from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


def test_health_returns_ok():
    """GET /health returns HTTP 200 with status and application info."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application": "NoteTutor AI",
    }