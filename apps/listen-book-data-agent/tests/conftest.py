"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class FakeQueryService:
    """Stub QueryService for HTTP-layer tests without real LLM/DB."""

    def __init__(self, result: dict | None = None):
        self.result = result or {
            "request_id": "fake-request-id",
            "status": "completed",
            "sql": "SELECT 1 AS value",
            "columns": ["value"],
            "rows": [{"value": 1}],
            "row_count": 1,
            "truncated": False,
            "metrics": ["play_count"],
            "time_range": "最近7天",
            "explanation": "测试解释",
            "duration_ms": 100,
            "error": None,
        }

    async def query_sync(
        self,
        query: str,
        parameters: dict | None = None,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        user_id: str | None = None,
        access_policy=None,
    ) -> dict:
        return {**self.result, "request_id": "fake-request-id"}

    async def query(
        self,
        query: str,
        parameters: dict | None = None,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        user_id: str | None = None,
        access_policy=None,
    ):
        yield 'event: context\ndata: {"type": "context", "request_id": "fake-request-id"}\n\n'
        yield 'event: done\ndata: {"type": "done", "request_id": "fake-request-id", "status": "completed", "duration_ms": 100}\n\n'

    async def validate_conversation_context(self, **kwargs):
        return None


@pytest.fixture
def fake_query_service_result():
    return {
        "request_id": "fake-request-id",
        "status": "completed",
        "sql": "SELECT 1 AS value",
        "columns": ["value"],
        "rows": [{"value": 1}],
        "row_count": 1,
        "truncated": False,
        "metrics": ["play_count"],
        "time_range": "最近7天",
        "explanation": "测试解释",
        "duration_ms": 100,
        "error": None,
    }


@pytest.fixture
def client(monkeypatch, fake_query_service_result):
    """Return a FastAPI TestClient with QueryService and auth dependencies overridden."""
    from app.agent.dependencies import get_query_service
    from app.api.deps import get_current_user
    from app.models.mysql.user_mysql import UserMySQL
    from main import app

    async def _override():
        return FakeQueryService(fake_query_service_result)

    async def _fake_user():
        return UserMySQL(
            id="test-user",
            username="tester",
            password_hash="",
            role="user",
            must_change_password=False,
            data_scope='[{"column":"region","value":"test"}]',
        )

    app.dependency_overrides[get_query_service] = _override
    app.dependency_overrides[get_current_user] = _fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()
