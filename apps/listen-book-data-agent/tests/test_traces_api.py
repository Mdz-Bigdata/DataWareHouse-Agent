"""查询记录接口的属主隔离测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.models.mysql.user_mysql import UserMySQL


class FakeTraceRepository:
    def __init__(self):
        self.list_calls: list[tuple[str, int]] = []
        self.delete_calls: list[str] = []
        self.sources: dict[str, SimpleNamespace] = {}

    async def list_for_user(self, user_id: str, limit: int = 50):
        self.list_calls.append((user_id, limit))
        return [
            SimpleNamespace(
                id="trace-1",
                query_text="平台一共有多少个有声专辑",
                status="completed",
                total_duration_ms=120,
                started_at=datetime(2026, 7, 17, 10, 0, 0),
                completed_at=None,
                conversation_id=None,
                parent_trace_id=None,
                regenerate_of_trace_id=None,
                standalone_question="平台一共有多少个有声专辑",
            )
        ]

    async def delete_for_user(self, user_id: str):
        self.delete_calls.append(user_id)
        return 3

    async def get_for_user(self, trace_id: str, user_id: str):
        source = self.sources.get(trace_id)
        if source is None or source.user_id != user_id:
            return None
        return source


class CapturingQueryService:
    def __init__(self):
        self.validation_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.sync_calls: list[dict] = []

    async def validate_conversation_context(self, **kwargs):
        self.validation_calls.append(kwargs)

    async def query(self, query: str, **kwargs):
        self.query_calls.append({"query": query, **kwargs})
        yield 'event: context\ndata: {"type": "context"}\n\n'
        yield 'event: done\ndata: {"type": "done", "status": "completed"}\n\n'

    async def query_sync(self, query: str, **kwargs):
        self.sync_calls.append({"query": query, **kwargs})
        return {"request_id": "sync-branch", "status": "completed"}


def _user(user_id: str) -> UserMySQL:
    return UserMySQL(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="",
        role="user",
        must_change_password=False,
    )


@pytest.fixture
def trace_client():
    from app.agent.dependencies import get_query_trace_repository
    from app.api.deps import get_current_user
    from main import app

    repository = FakeTraceRepository()
    state = {"user_id": "user-A"}

    async def _repo():
        return repository

    async def _current():
        return _user(state["user_id"])

    app.dependency_overrides[get_query_trace_repository] = _repo
    app.dependency_overrides[get_current_user] = _current
    yield TestClient(app), repository, state
    app.dependency_overrides.clear()


@pytest.fixture
def branch_client():
    from app.agent.dependencies import get_query_service, get_query_trace_repository
    from app.api.deps import get_current_user
    from main import app

    repository = FakeTraceRepository()
    service = CapturingQueryService()
    state = {"user_id": "user-A"}

    async def _repo():
        return repository

    async def _service():
        return service

    async def _current():
        user = _user(state["user_id"])
        user.role = "admin"
        return user

    app.dependency_overrides[get_query_trace_repository] = _repo
    app.dependency_overrides[get_query_service] = _service
    app.dependency_overrides[get_current_user] = _current
    yield TestClient(app), repository, service, state
    app.dependency_overrides.clear()


def test_list_traces_scoped_to_current_user(trace_client):
    client, repository, state = trace_client

    response = client.get("/api/traces")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["id"] == "trace-1"
    assert data[0]["query_text"] == "平台一共有多少个有声专辑"
    assert repository.list_calls == [("user-A", 50)]

    # 换一个登录用户，查询的是另一个属主的记录
    state["user_id"] = "user-B"
    client.get("/api/traces")
    assert repository.list_calls[-1] == ("user-B", 50)


def test_clear_traces_scoped_to_current_user(trace_client):
    client, repository, _ = trace_client
    response = client.delete("/api/traces")
    assert response.status_code == 200
    assert response.json() == {"deleted": 3}
    assert repository.delete_calls == ["user-A"]


def test_query_endpoints_forward_optional_conversation_branch_fields(branch_client):
    client, _, service, _ = branch_client

    response = client.post(
        "/api/query",
        json={
            "query": "换成周粒度",
            "conversation_id": "conversation-1",
            "parent_trace_id": "trace-parent",
        },
    )
    assert response.status_code == 200
    assert service.validation_calls[-1] == {
        "user_id": "user-A",
        "conversation_id": "conversation-1",
        "parent_trace_id": "trace-parent",
    }
    assert service.query_calls[-1]["conversation_id"] == "conversation-1"
    assert service.query_calls[-1]["parent_trace_id"] == "trace-parent"

    legacy_response = client.post("/api/query/sync", json={"query": "播放量是多少"})
    assert legacy_response.status_code == 200
    assert service.sync_calls[-1]["conversation_id"] is None
    assert service.sync_calls[-1]["parent_trace_id"] is None


def test_regenerate_creates_a_sibling_branch_without_mutating_source(branch_client):
    client, repository, service, _ = branch_client
    source = SimpleNamespace(
        id="trace-source",
        user_id="user-A",
        query_text="最近三个月播放趋势",
        conversation_id="conversation-1",
        parent_trace_id="trace-root",
    )
    repository.sources[source.id] = source
    before = vars(source).copy()

    response = client.post(
        "/api/traces/trace-source/regenerate",
        json={"parameters": {"grain": "week"}},
    )

    assert response.status_code == 200
    assert vars(source) == before
    call = service.query_calls[-1]
    assert call["query"] == "最近三个月播放趋势"
    assert call["parameters"] == {"grain": "week"}
    assert call["conversation_id"] == "conversation-1"
    assert call["parent_trace_id"] == "trace-root"
    assert call["regenerate_of_trace_id"] == "trace-source"
    assert call["user_id"] == "user-A"
    assert call["access_policy"].admin_bypass is True


def test_regenerate_is_owner_scoped_and_rejects_single_turn_trace(branch_client):
    client, repository, _, state = branch_client
    repository.sources["single-turn"] = SimpleNamespace(
        id="single-turn",
        user_id="user-A",
        query_text="播放量",
        conversation_id=None,
        parent_trace_id=None,
    )
    assert client.post("/api/traces/single-turn/regenerate", json={}).status_code == 409

    state["user_id"] = "user-B"
    assert client.post("/api/traces/single-turn/regenerate", json={}).status_code == 404


def test_traces_require_auth():
    from app.agent.dependencies import (
        get_meta_session,
        get_query_service,
        get_query_trace_repository,
    )
    from main import app

    async def _fake_session():
        yield object()

    async def _fake_service():
        return CapturingQueryService()

    async def _fake_trace_repository():
        return FakeTraceRepository()

    app.dependency_overrides[get_meta_session] = _fake_session
    app.dependency_overrides[get_query_service] = _fake_service
    app.dependency_overrides[get_query_trace_repository] = _fake_trace_repository
    try:
        client = TestClient(app)
        assert client.get("/api/traces").status_code == 401
        assert client.delete("/api/traces").status_code == 401
        assert (
            client.post(
                "/api/traces/trace-1/feedback",
                json={"verdict": "correct", "reasons": ["accurate"]},
            ).status_code
            == 401
        )
        assert client.post("/api/traces/trace-1/regenerate", json={}).status_code == 401
    finally:
        app.dependency_overrides.clear()
