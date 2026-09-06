"""HTTP API layer tests using FastAPI TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Tests in this file intentionally avoid real LLM/DB by overriding the
# QueryService dependency in conftest.py.


def test_health_endpoint(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_debug_page_renders(client: TestClient):
    response = client.get("/debug")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "查询调试" in response.text


def test_query_sync_endpoint(client: TestClient):
    response = client.post(
        "/api/query/sync",
        json={"query": "测试问题"},
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["sql"] == "SELECT 1 AS value"
    assert data["row_count"] == 1
    assert data["request_id"] == "fake-request-id"


def test_query_sync_endpoint_returns_error_when_service_fails(client: TestClient, monkeypatch):
    from app.agent.dependencies import get_query_service
    from main import app

    class FailingQueryService:
        async def query_sync(
            self,
            query: str,
            parameters: dict | None = None,
            conversation_id: str | None = None,
            parent_trace_id: str | None = None,
            regenerate_of_trace_id: str | None = None,
            user_id: str | None = None,
            access_policy=None,
        ):
            raise RuntimeError("service failure")

        async def validate_conversation_context(self, **kwargs):
            return None

    async def _override():
        return FailingQueryService()

    app.dependency_overrides[get_query_service] = _override
    try:
        with pytest.raises(RuntimeError, match="service failure"):
            client.post(
                "/api/query/sync",
                json={"query": "测试问题"},
            )
    finally:
        app.dependency_overrides.clear()


def test_query_stream_endpoint(client: TestClient):
    response = client.post(
        "/api/query",
        json={"query": "测试问题"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert "event: context" in text
    assert "event: done" in text


def test_query_endpoints_require_auth():
    """不覆盖 get_current_user 时，未带 Bearer 令牌的查询请求必须 401。"""
    from conftest import FakeQueryService

    from app.agent.dependencies import get_meta_session, get_query_service
    from main import app

    async def _fake_session():
        yield object()

    async def _fake_service():
        return FakeQueryService()

    app.dependency_overrides[get_meta_session] = _fake_session
    app.dependency_overrides[get_query_service] = _fake_service
    try:
        unauthenticated = TestClient(app)
        for path in ("/api/query", "/api/query/sync"):
            response = unauthenticated.post(path, json={"query": "测试问题"})
            assert response.status_code == 401, path
    finally:
        app.dependency_overrides.clear()


def test_query_endpoint_rejects_ordinary_user_without_policy(client: TestClient):
    from app.api.deps import get_current_user
    from app.models.mysql.user_mysql import UserMySQL
    from main import app

    async def _user_without_policy():
        return UserMySQL(
            id="no-policy-user",
            username="no-policy",
            password_hash="",
            role="user",
            must_change_password=False,
            data_scope=None,
        )

    app.dependency_overrides[get_current_user] = _user_without_policy
    response = client.post("/api/query/sync", json={"query": "测试问题"})

    assert response.status_code == 403
    assert response.json()["detail"] == "普通用户缺少访问策略"
