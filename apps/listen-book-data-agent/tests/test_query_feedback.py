from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.mysql.base import Base
from app.models.mysql.query_feedback_mysql import (
    QueryFeedbackMySQL,
    QueryTemplateConfidenceMySQL,
)
from app.models.mysql.query_trace_mysql import QueryTraceMySQL
from app.models.mysql.verified_query_mysql import VerifiedQueryRevisionMySQL
from app.repositories.mysql.query_feedback_repository import QueryFeedbackRepository
from app.repositories.mysql.verified_query_repository import VerifiedQueryRepository
from app.services.query_feedback_service import QueryFeedbackService


async def _with_session(callback):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await callback(session)
    await engine.dispose()


def _trace(trace_id: str, *, user_id: str = "user-1", sql: str | None = None):
    return QueryTraceMySQL(
        id=trace_id,
        user_id=user_id,
        query_text="查询手机号 13800138000 的已支付订单",
        status="completed",
        sql=sql
        or (
            "SELECT o.id, o.total AS amount FROM orders o "
            "WHERE o.status = 'paid' AND o.region = '华东' LIMIT 500"
        ),
        policy_version="policy-v1",
        policy_hash="a" * 64,
        policy_admin_bypass=False,
    )


def _service(session):
    return QueryFeedbackService(
        QueryFeedbackRepository(session),
        VerifiedQueryRepository(session),
    )


def test_incorrect_feedback_creates_only_a_redacted_candidate_revision():
    async def scenario(session):
        session.add(_trace("trace-negative"))
        await session.commit()

        result = await _service(session).submit(
            trace_id="trace-negative",
            user_id="user-1",
            verdict="incorrect",
            reasons=["wrong_filter", "wrong_filter"],
            comment="手机号 13800138000 的状态筛选不对",
            domain="audio",
            datasource="audio_full",
            row_level_scope=[{"table": "orders", "column": "region", "value": "华东"}],
        )

        assert result.verdict == "incorrect"
        assert result.reasons == ["wrong_filter"]
        assert result.candidate_revision_id is not None
        assert result.positive_count is None
        assert "13800138000" not in result.comment
        candidate = await session.get(
            VerifiedQueryRevisionMySQL, result.candidate_revision_id
        )
        assert candidate.lifecycle == "candidate"
        assert candidate.source == "feedback"
        assert candidate.source_trace_id == "trace-negative"
        assert "华东" not in candidate.sql_template
        assert "region" not in candidate.sql_template
        assert "paid" not in candidate.sql_template
        assert ":p1" in candidate.sql_template
        assert candidate.parameter_schema == [
            {"name": "p1", "type": "string", "required": True}
        ]
        assert (
            await session.scalar(select(func.count()).select_from(QueryTemplateConfidenceMySQL))
            == 0
        )

    asyncio.run(_with_session(scenario))


def test_correct_feedback_only_accumulates_template_confidence():
    async def scenario(session):
        session.add_all([_trace("trace-good-1"), _trace("trace-good-2")])
        await session.commit()
        service = _service(session)
        first = await service.submit(
            trace_id="trace-good-1",
            user_id="user-1",
            verdict="correct",
            reasons=["accurate"],
            comment="结果正确",
            domain="audio",
            datasource="audio_full",
            row_level_scope=[{"table": "orders", "column": "region", "value": "华东"}],
        )
        second = await service.submit(
            trace_id="trace-good-2",
            user_id="user-1",
            verdict="correct",
            reasons=["helpful"],
            comment="",
            domain="audio",
            datasource="audio_full",
            row_level_scope=[{"table": "orders", "column": "region", "value": "华东"}],
        )

        assert first.positive_count == 1
        assert second.positive_count == 2
        confidence = await session.get(
            QueryTemplateConfidenceMySQL, first.template_signature
        )
        assert confidence.positive_count == 2
        assert confidence.last_trace_id == "trace-good-2"
        assert (
            await session.scalar(select(func.count()).select_from(VerifiedQueryRevisionMySQL))
            == 0
        )

    asyncio.run(_with_session(scenario))


def test_feedback_is_owner_scoped_single_submission_and_reason_typed():
    async def scenario(session):
        session.add(_trace("trace-owned"))
        await session.commit()
        service = _service(session)
        with pytest.raises(LookupError, match="不存在"):
            await service.submit(
                trace_id="trace-owned",
                user_id="other-user",
                verdict="correct",
                reasons=["accurate"],
                comment="",
                domain="audio",
                datasource="audio_full",
                row_level_scope=[],
            )
        with pytest.raises(ValueError, match="不匹配"):
            await service.submit(
                trace_id="trace-owned",
                user_id="user-1",
                verdict="correct",
                reasons=["wrong_join"],
                comment="",
                domain="audio",
                datasource="audio_full",
                row_level_scope=[],
            )
        await service.submit(
            trace_id="trace-owned",
            user_id="user-1",
            verdict="correct",
            reasons=["accurate"],
            comment="",
            domain="audio",
            datasource="audio_full",
            row_level_scope=[],
        )
        with pytest.raises(ValueError, match="已经提交"):
            await service.submit(
                trace_id="trace-owned",
                user_id="user-1",
                verdict="incorrect",
                reasons=["wrong_join"],
                comment="",
                domain="audio",
                datasource="audio_full",
                row_level_scope=[],
            )
        assert await session.scalar(select(func.count()).select_from(QueryFeedbackMySQL)) == 1

    asyncio.run(_with_session(scenario))
