"""认证接口与 JWT 令牌测试。DB 通过依赖覆盖与 monkeypatch 隔离。"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.conf.app_config import app_config
from app.core.security import create_access_token, decode_access_token
from app.models.mysql.user_mysql import UserMySQL


def _fake_user(**overrides) -> UserMySQL:
    data = {
        "id": "user-1",
        "username": "admin",
        "password_hash": "pbkdf2_sha256$1$aa$bb",
        "role": "admin",
        "must_change_password": False,
    }
    data.update(overrides)
    return UserMySQL(**data)


@pytest.fixture
def auth_client(monkeypatch):
    """TestClient：meta session 用空壳覆盖，AuthService 方法按需 monkeypatch。"""
    from main import app
    from app.agent.dependencies import get_meta_session

    async def _fake_session():
        yield object()

    app.dependency_overrides[get_meta_session] = _fake_session
    yield TestClient(app), monkeypatch
    app.dependency_overrides.clear()


def test_token_roundtrip():
    token = create_access_token(user_id="u1", username="admin", role="admin")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "u1"
    assert payload["role"] == "admin"
    assert payload["exp"] > time.time()


def test_token_rejects_tampered_signature():
    token = create_access_token(user_id="u1", username="admin", role="admin")
    forged = jwt.encode(
        {"sub": "u1", "role": "admin", "exp": int(time.time()) + 3600},
        "wrong-secret",
        algorithm="HS256",
    )
    assert decode_access_token(forged) is None
    assert decode_access_token(token[:-2] + "xx") is None
    assert decode_access_token("not-a-jwt") is None


def test_token_rejects_expired():
    expired = jwt.encode(
        {"sub": "u1", "role": "admin", "exp": int(time.time()) - 10},
        app_config.auth.secret_key,
        algorithm="HS256",
    )
    assert decode_access_token(expired) is None


def test_login_success(auth_client):
    client, monkeypatch = auth_client
    from app.services.auth_service import AuthService

    async def _ok(self, username, password):
        return _fake_user()

    monkeypatch.setattr(AuthService, "verify_login", _ok)
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["token"]
    assert data["token_type"] == "bearer"
    assert data["expires_in_minutes"] == app_config.auth.token_ttl_minutes
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "admin"
    assert "password_hash" not in response.text
    assert "pbkdf2" not in response.text


def test_login_wrong_password_returns_401(auth_client):
    client, monkeypatch = auth_client
    from app.services.auth_service import AuthService

    async def _fail(self, username, password):
        return None

    monkeypatch.setattr(AuthService, "verify_login", _fail)
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert response.status_code == 401


def test_me_requires_token(auth_client):
    client, _ = auth_client
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_valid_token(auth_client):
    client, monkeypatch = auth_client
    from app.services.auth_service import AuthService

    async def _get(self, user_id):
        return _fake_user(id=user_id)

    monkeypatch.setattr(AuthService, "get_user_by_id", _get)
    token = create_access_token(user_id="user-1", username="admin", role="admin")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["id"] == "user-1"


def test_change_password_wrong_old_returns_400(auth_client):
    client, monkeypatch = auth_client
    from app.services.auth_service import AuthService

    async def _get(self, user_id):
        return _fake_user(id=user_id)

    async def _change(self, user, old, new):
        return False

    monkeypatch.setattr(AuthService, "get_user_by_id", _get)
    monkeypatch.setattr(AuthService, "change_password", _change)
    token = create_access_token(user_id="user-1", username="admin", role="admin")
    response = client.post(
        "/api/auth/change-password",
        json={"old_password": "bad", "new_password": "newpass1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
