"""LLM 供应商管理接口测试：sqlite 内存库隔离真实 MySQL，连接测试打桩。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.mysql.base import Base
from app.models.mysql.user_mysql import UserMySQL


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, autoflush=False, autobegin=True, autocommit=False, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


def _user(role: str) -> UserMySQL:
    return UserMySQL(
        id=f"u-{role}",
        username=role,
        password_hash="",
        role=role,
        must_change_password=False,
    )


@pytest.fixture
def admin_client(session_factory):
    """按角色切换当前用户的管理接口 TestClient。"""
    from main import app
    from app.agent.dependencies import get_meta_session
    from app.api.deps import get_current_user

    state = {"role": "admin"}

    async def _session():
        async with session_factory() as session:
            yield session

    async def _current():
        return _user(state["role"])

    app.dependency_overrides[get_meta_session] = _session
    app.dependency_overrides[get_current_user] = _current
    yield TestClient(app), state, session_factory
    app.dependency_overrides.clear()


def _payload(**overrides):
    data = {
        "name": "测试供应商",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "api_key": "sk-test-1234567890",
        "temperature": 0.0,
        "timeout_seconds": 60,
    }
    data.update(overrides)
    return data


def test_admin_endpoints_require_admin_role(admin_client):
    client, state, _ = admin_client
    state["role"] = "user"
    assert client.get("/api/admin/llm-providers").status_code == 403
    assert client.post("/api/admin/llm-providers", json=_payload()).status_code == 403


def test_create_list_and_mask(admin_client):
    client, _, factory = admin_client
    response = client.post("/api/admin/llm-providers", json=_payload())
    assert response.status_code == 201
    item = response.json()
    assert item["api_key_masked"] == "sk-****7890"
    assert "sk-test-1234567890" not in response.text

    listing = client.get("/api/admin/llm-providers")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    # 落库为密文
    import asyncio
    from sqlalchemy import select
    from app.models.mysql.llm_provider_mysql import LlmProviderMySQL

    async def _check():
        async with factory() as session:
            result = await session.execute(select(LlmProviderMySQL))
            row = result.scalar_one()
            assert row.api_key_encrypted != "sk-test-1234567890"
            assert "sk-test" not in row.api_key_encrypted

    asyncio.run(_check())


def test_create_requires_api_key(admin_client):
    client, _, _ = admin_client
    response = client.post("/api/admin/llm-providers", json=_payload(api_key=""))
    assert response.status_code == 400


def test_update_keeps_key_when_blank(admin_client):
    client, _, _ = admin_client
    created = client.post("/api/admin/llm-providers", json=_payload()).json()
    updated = client.put(
        f"/api/admin/llm-providers/{created['id']}",
        json=_payload(name="改名", api_key="", model_name="deepseek-reasoner"),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名"
    assert updated.json()["model_name"] == "deepseek-reasoner"
    assert updated.json()["api_key_masked"] == "sk-****7890"  # 原密钥未变


def test_activate_is_exclusive(admin_client):
    client, _, _ = admin_client
    first = client.post("/api/admin/llm-providers", json=_payload(name="甲")).json()
    second = client.post("/api/admin/llm-providers", json=_payload(name="乙")).json()

    client.post(f"/api/admin/llm-providers/{first['id']}/activate")
    client.post(f"/api/admin/llm-providers/{second['id']}/activate")

    items = client.get("/api/admin/llm-providers").json()
    active = [item for item in items if item["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == second["id"]


def test_delete_active_is_blocked(admin_client):
    client, _, _ = admin_client
    created = client.post("/api/admin/llm-providers", json=_payload()).json()
    client.post(f"/api/admin/llm-providers/{created['id']}/activate")
    assert client.delete(f"/api/admin/llm-providers/{created['id']}").status_code == 400


def test_delete_inactive_ok(admin_client):
    client, _, _ = admin_client
    created = client.post("/api/admin/llm-providers", json=_payload()).json()
    assert client.delete(f"/api/admin/llm-providers/{created['id']}").status_code == 200
    assert client.get("/api/admin/llm-providers").json() == []


def test_missing_provider_returns_404(admin_client):
    client, _, _ = admin_client
    assert client.post("/api/admin/llm-providers/nope/activate").status_code == 404
    assert client.delete("/api/admin/llm-providers/nope").status_code == 404


def test_connection_endpoint_uses_stored_key(admin_client, monkeypatch):
    client, _, _ = admin_client
    created = client.post("/api/admin/llm-providers", json=_payload()).json()

    captured = {}

    async def _fake_test(**kwargs):
        captured.update(kwargs)
        return True, 123, None

    monkeypatch.setattr(
        "app.services.llm_provider_service.LlmProviderService.test_connection",
        _fake_test,
    )
    response = client.post(f"/api/admin/llm-providers/{created['id']}/test", json=None)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "latency_ms": 123, "error": None}
    assert captured["api_key"] == "sk-test-1234567890"
