import os
import uuid

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PASSWORD_ITERATIONS", "1000")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("SECRET_KEY", "test-secret-key-value-with-enough-length")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ams.db'}")
    monkeypatch.setenv("PASSWORD_ITERATIONS", "1000")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-value-with-enough-length")
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "")
    monkeypatch.setenv("SERVICENOW_USERNAME", "")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from app.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


def login(client, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def auth_admin(client):
    return login(client, "admin@ams.local", "LocalAdmin!2026")


def auth_resolver(client):
    return login(client, "resolver@ams.local", "LocalResolver!2026")


def auth_priya(client):
    return login(client, "priya.shah@ams.local", "LocalReviewer!2026")


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}
