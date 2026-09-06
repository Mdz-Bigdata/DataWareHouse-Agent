from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.mysql.base import Base
from app.models.mysql.query_trace_mysql import QueryTraceMySQL
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.query_trace_repository import QueryTraceRepository


@pytest_asyncio.fixture
async def conversation_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def conversation_client(conversation_session_factory):
    from app.agent.dependencies import get_query_trace_repository
    from app.api.deps import get_current_user
    from main import app

    state = {"user_id": "user-1"}

    async def _repository():
        async with conversation_session_factory() as session:
            yield QueryTraceRepository(session)

    async def _current_user():
        return UserMySQL(
            id=state["user_id"],
            username=state["user_id"],
            password_hash="",
            role="user",
            must_change_password=False,
            data_scope='[{"table":"orders","column":"region","value":"华东"}]',
        )

    app.dependency_overrides[get_query_trace_repository] = _repository
    app.dependency_overrides[get_current_user] = _current_user
    yield TestClient(app), state, conversation_session_factory
    app.dependency_overrides.clear()


def test_conversation_crud_search_archive_and_owner_scope(conversation_client):
    client, state, _ = conversation_client

    created = client.post("/api/conversations", json={"title": " 播放趋势分析 "})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["title"] == "播放趋势分析"

    assert client.get("/api/conversations?search=播放").json()[0]["id"] == conversation_id
    renamed = client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "渠道趋势"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "渠道趋势"

    archived = client.patch(
        f"/api/conversations/{conversation_id}",
        json={"status": "archived"},
    )
    assert archived.status_code == 200
    assert client.get("/api/conversations").json() == []
    assert client.get("/api/conversations?include_archived=true").json()[0]["status"] == "archived"

    state["user_id"] = "user-2"
    assert client.get(f"/api/conversations/{conversation_id}/turns").status_code == 404
    assert client.patch(
        f"/api/conversations/{conversation_id}", json={"title": "越权修改"}
    ).status_code == 404


def test_conversation_turn_restore_returns_summaries_but_never_result_rows(conversation_client):
    client, _, factory = conversation_client
    conversation_id = client.post(
        "/api/conversations", json={"title": "可恢复会话"}
    ).json()["id"]

    async def seed():
        async with factory() as session:
            session.add(
                QueryTraceMySQL(
                    id="trace-restore",
                    user_id="user-1",
                    conversation_id=conversation_id,
                    parent_trace_id=None,
                    regenerate_of_trace_id=None,
                    query_text="最近7天播放趋势",
                    standalone_question="统计最近7天按天播放次数趋势",
                    query_plan_summary={"intent": "trend", "time_grain": "day"},
                    answer_summary="播放次数整体上升。",
                    chart_spec={"type": "line", "x": "date", "y": ["play_count"]},
                    status="completed",
                    sql="SELECT play_date, COUNT(*) FROM play_session GROUP BY play_date",
                    total_duration_ms=120,
                    policy_version="policy-v2",
                    policy_hash="b" * 64,
                    policy_admin_bypass=False,
                )
            )
            await session.commit()

    asyncio.run(seed())
    response = client.get(f"/api/conversations/{conversation_id}/turns")

    assert response.status_code == 200
    turn = response.json()[0]
    assert turn["standalone_question"].startswith("统计最近7天")
    assert turn["query_plan_summary"]["intent"] == "trend"
    assert turn["answer_summary"] == "播放次数整体上升。"
    assert turn["chart_spec"]["type"] == "line"
    assert "rows" not in turn
    assert "result_rows" not in turn


def test_conversation_endpoints_require_auth(conversation_session_factory):
    from app.agent.dependencies import get_meta_session, get_query_trace_repository
    from main import app

    async def _repository():
        async with conversation_session_factory() as session:
            yield QueryTraceRepository(session)

    async def _meta_session():
        yield object()

    app.dependency_overrides[get_query_trace_repository] = _repository
    app.dependency_overrides[get_meta_session] = _meta_session
    try:
        client = TestClient(app)
        assert client.get("/api/conversations").status_code == 401
        assert client.post("/api/conversations", json={}).status_code == 401
    finally:
        app.dependency_overrides.clear()
