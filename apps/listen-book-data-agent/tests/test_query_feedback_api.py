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
async def feedback_session_factory():
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
def feedback_client(feedback_session_factory):
    from app.agent.dependencies import get_query_trace_repository
    from app.api.deps import get_current_user
    from main import app

    state = {"user_id": "user-1"}

    async def seed():
        async with feedback_session_factory() as session:
            session.add(
                QueryTraceMySQL(
                    id="trace-api",
                    user_id="user-1",
                    query_text="查询已支付订单",
                    status="completed",
                    sql="SELECT id FROM orders WHERE status = 'paid' LIMIT 500",
                    policy_version="policy-v1",
                    policy_hash="a" * 64,
                    policy_admin_bypass=False,
                )
            )
            await session.commit()

    asyncio.run(seed())

    async def _repository():
        async with feedback_session_factory() as session:
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
    yield TestClient(app), state
    app.dependency_overrides.clear()


def test_feedback_endpoint_creates_candidate_once_and_enforces_owner(feedback_client):
    client, state = feedback_client
    response = client.post(
        "/api/traces/trace-api/feedback",
        json={
            "verdict": "incorrect",
            "reasons": ["wrong_filter"],
            "comment": "状态条件不正确",
        },
    )
    assert response.status_code == 201
    assert response.json()["candidate_revision_id"]
    assert response.json()["positive_count"] is None

    duplicate = client.post(
        "/api/traces/trace-api/feedback",
        json={"verdict": "correct", "reasons": ["accurate"]},
    )
    assert duplicate.status_code == 409

    state["user_id"] = "user-2"
    not_owned = client.post(
        "/api/traces/trace-api/feedback",
        json={"verdict": "correct", "reasons": ["accurate"]},
    )
    assert not_owned.status_code == 404


def test_feedback_endpoint_rejects_reason_that_does_not_match_verdict(feedback_client):
    client, _ = feedback_client
    response = client.post(
        "/api/traces/trace-api/feedback",
        json={"verdict": "correct", "reasons": ["wrong_join"]},
    )
    assert response.status_code == 409
