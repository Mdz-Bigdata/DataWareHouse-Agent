from __future__ import annotations

import asyncio

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agent.dependencies import get_query_service, get_query_trace_repository
from app.api.deps import get_current_user
from app.models.mysql.base import Base
from app.models.mysql.query_trace_mysql import QueryTraceMySQL
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.insight_card_repository import InsightCardRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.insight_card_service import InsightCardService


@pytest_asyncio.fixture
async def insight_session_factory():
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


async def _seed_trace(factory) -> None:
    async with factory() as session:
        session.add(
            QueryTraceMySQL(
                id="trace-card",
                user_id="user-1",
                query_text="查看华东已支付订单趋势",
                standalone_question="查看华东已支付订单趋势",
                status="completed",
                sql=(
                    "SELECT order_date, COUNT(*) AS order_count FROM content_order "
                    "WHERE status = 'paid' AND region = 'east' "
                    "GROUP BY order_date LIMIT 500"
                ),
                answer_summary="已支付订单趋势保持稳定。",
                chart_spec={
                    "schema_version": "chart-spec/v1",
                    "type": "line",
                    "title": "订单趋势",
                    "dimension": "order_date",
                    "metrics": ["order_count"],
                    "series": None,
                    "source": "deterministic",
                },
                build_id="build-1",
                semantic_release_id="release-2",
                semantic_release_version=2,
                query_set_id="query-set-3",
                query_set_version=3,
                business_rule_set_id="rule-set-4",
                business_rule_set_version=4,
                policy_version="policy-v5",
                policy_hash="a" * 64,
            )
        )
        await session.commit()


def test_insight_card_saves_only_parameterized_metadata(insight_session_factory):
    async def scenario():
        await _seed_trace(insight_session_factory)
        async with insight_session_factory() as session:
            card = await InsightCardService(
                InsightCardRepository(session),
                QueryTraceRepository(session),
            ).save_from_trace(
                trace_id="trace-card",
                user_id="user-1",
                row_level_scope=[
                    {"table": "content_order", "column": "region", "value": "east"}
                ],
                dialect="mysql",
            )

            assert card.question == "查看华东已支付订单趋势"
            assert card.answer_summary == "已支付订单趋势保持稳定。"
            assert "east" not in card.sql_template
            assert "paid" not in card.sql_template
            assert ":p1" in card.sql_template
            assert card.parameter_types == ["string", "string"]
            assert card.sql_template.count(":p") == len(card.parameter_types)
            assert card.chart_spec["type"] == "line"
            assert card.version_info["query_set_version"] == 3
            assert "rows" not in card.__table__.columns
            assert "result_rows" not in card.__table__.columns

            assert await InsightCardRepository(session).get_for_user(card.id, "other") is None

    asyncio.run(scenario())


def test_insight_card_api_is_owner_scoped_and_reexecutes_with_current_policy(
    insight_session_factory,
):
    from main import app

    asyncio.run(_seed_trace(insight_session_factory))
    state = {"user_id": "user-1"}

    class FakeQueryService:
        def __init__(self):
            self.calls = []

        async def query(self, query: str, **kwargs):
            self.calls.append((query, kwargs))
            yield 'event: context\ndata: {"type":"context","request_id":"rerun-1"}\n\n'
            yield (
                'event: done\ndata: {"type":"done","request_id":"rerun-1",'
                '"status":"completed","duration_ms":1,"error":null}\n\n'
            )

    query_service = FakeQueryService()

    async def _trace_repository():
        async with insight_session_factory() as session:
            yield QueryTraceRepository(session)

    async def _query_service():
        return query_service

    async def _current_user():
        return UserMySQL(
            id=state["user_id"],
            username=state["user_id"],
            password_hash="",
            role="admin",
            must_change_password=False,
        )

    app.dependency_overrides[get_query_trace_repository] = _trace_repository
    app.dependency_overrides[get_query_service] = _query_service
    app.dependency_overrides[get_current_user] = _current_user
    try:
        client = TestClient(app)
        saved = client.post("/api/insight-cards/from-trace/trace-card")
        assert saved.status_code == 201
        card_id = saved.json()["id"]
        assert "rows" not in saved.json()
        assert "paid" not in saved.json()["sql_template"]

        listed = client.get("/api/insight-cards")
        assert [item["id"] for item in listed.json()] == [card_id]

        executed = client.post(
            f"/api/insight-cards/{card_id}/execute",
            json={"conversation_id": "conversation-current"},
        )
        assert executed.status_code == 200
        assert "rerun-1" in executed.text
        assert query_service.calls[0][0] == "查看华东已支付订单趋势"
        assert query_service.calls[0][1]["user_id"] == "user-1"
        assert query_service.calls[0][1]["conversation_id"] == "conversation-current"
        assert query_service.calls[0][1]["access_policy"].admin_bypass is True

        state["user_id"] = "other-user"
        assert client.get("/api/insight-cards").json() == []
        assert client.post(f"/api/insight-cards/{card_id}/execute").status_code == 404

        state["user_id"] = "user-1"
        assert client.delete(f"/api/insight-cards/{card_id}").status_code == 204
        assert client.get("/api/insight-cards").json() == []
    finally:
        app.dependency_overrides.clear()
