from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.context import set_request_id
from app.models.mysql.base import Base
from app.models.mysql.conversation_mysql import ConversationMySQL
from app.models.mysql.query_trace_mysql import QueryTraceMySQL
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.conversation_repository import ConversationRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.access_policy import resolve_access_policy
from app.services.query_service import ConversationContextError, QueryService


async def _with_session(callback):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await callback(session)
    await engine.dispose()


def test_conversation_and_branch_trace_persist_summaries_without_result_rows():
    async def scenario(session):
        conversations = ConversationRepository(session)
        traces = QueryTraceRepository(session)
        conversation = ConversationMySQL(
            id="conversation-1",
            user_id="user-1",
            title="播放趋势分析",
            status="active",
        )
        await conversations.add(conversation)
        await session.commit()
        await traces.create_trace(
            "trace-parent",
            "最近三个月播放趋势",
            "user-1",
            conversation_id=conversation.id,
            standalone_question="统计最近三个月按月播放次数趋势",
        )
        await traces.finish_trace(
            trace_id="trace-parent",
            status="completed",
            total_duration_ms=120,
            sql="SELECT month, COUNT(*) AS play_count FROM play_session GROUP BY month",
            standalone_question="统计最近三个月按月播放次数趋势",
            query_plan_summary={"intent": "trend", "time_grain": "month"},
            answer_summary="最近三个月播放次数逐月上升。",
            chart_spec={"type": "line", "x": "month", "y": ["play_count"]},
        )
        await traces.create_trace(
            "trace-regenerated",
            "换成周粒度",
            "user-1",
            conversation_id=conversation.id,
            parent_trace_id="trace-parent",
            regenerate_of_trace_id="trace-parent",
            standalone_question="统计最近三个月按周播放次数趋势",
        )

        stored = await session.get(QueryTraceMySQL, "trace-parent")
        assert stored.conversation_id == conversation.id
        assert stored.query_plan_summary["intent"] == "trend"
        assert stored.answer_summary == "最近三个月播放次数逐月上升。"
        assert stored.chart_spec["type"] == "line"
        branch = await session.get(QueryTraceMySQL, "trace-regenerated")
        assert branch.parent_trace_id == "trace-parent"
        assert branch.regenerate_of_trace_id == "trace-parent"
        assert [item.id for item in await conversations.list_traces_for_user(
            conversation.id, "user-1"
        )] == ["trace-parent", "trace-regenerated"]
        assert (
            await conversations.list_traces_for_user(conversation.id, "other-user") == []
        )

    asyncio.run(_with_session(scenario))

    assert "rows" not in ConversationMySQL.__table__.columns
    assert "result_rows" not in ConversationMySQL.__table__.columns
    assert "rows" not in QueryTraceMySQL.__table__.columns
    assert "result_rows" not in QueryTraceMySQL.__table__.columns


def test_conversation_listing_is_owner_scoped_searchable_and_archive_aware():
    async def scenario(session):
        repository = ConversationRepository(session)
        await repository.add(
            ConversationMySQL(
                id="conversation-active",
                user_id="user-1",
                title="专辑分析",
                status="active",
            )
        )
        await repository.add(
            ConversationMySQL(
                id="conversation-archived",
                user_id="user-1",
                title="会员分析",
                status="archived",
            )
        )
        await repository.add(
            ConversationMySQL(
                id="conversation-other",
                user_id="user-2",
                title="专辑分析",
                status="active",
            )
        )
        await session.commit()

        assert [item.id for item in await repository.list_for_user("user-1")] == [
            "conversation-active"
        ]
        assert [
            item.id
            for item in await repository.list_for_user(
                "user-1", include_archived=True, search="会员"
            )
        ] == ["conversation-archived"]
        assert await repository.get_for_user("conversation-active", "user-2") is None

    asyncio.run(_with_session(scenario))


def test_query_service_validates_owner_and_persists_regenerate_as_new_branch():
    class EmptyGraph:
        def __init__(self):
            self.kwargs = None
            self.calls = 0

        async def astream(self, **kwargs):
            self.calls += 1
            self.kwargs = kwargs
            if False:
                yield kwargs

    async def scenario(session):
        conversations = ConversationRepository(session)
        traces = QueryTraceRepository(session)
        await conversations.add(
            ConversationMySQL(
                id="conversation-branch",
                user_id="user-1",
                title="播放趋势",
                status="active",
            )
        )
        await conversations.add(
            ConversationMySQL(
                id="conversation-other",
                user_id="user-2",
                title="其他用户会话",
                status="active",
            )
        )
        await session.commit()
        await traces.create_trace(
            "trace-root",
            "最近三个月播放趋势",
            "user-1",
            conversation_id="conversation-branch",
        )
        await traces.finish_trace(
            trace_id="trace-root",
            status="completed",
            total_duration_ms=10,
            sql="SELECT COUNT(*) FROM forbidden_legacy_table WHERE region = '华东'",
            answer_summary="根轮结果",
        )
        await traces.create_trace(
            "trace-source",
            "换成月粒度",
            "user-1",
            conversation_id="conversation-branch",
            parent_trace_id="trace-root",
            policy_version="old-policy-v1",
            policy_hash="old-policy-hash",
        )
        await traces.finish_trace(
            trace_id="trace-source",
            status="completed",
            total_duration_ms=10,
            answer_summary="原始结果",
        )
        graph_runner = EmptyGraph()
        service = QueryService(
            dw_mysql_repository=None,
            meta_mysql_repository=None,
            column_qdrant_repository=None,
            metric_qdrant_repository=None,
            value_es_repository=None,
            embedding_client=None,
            query_trace_repository=traces,
            graph_runner=graph_runner,
        )
        user = UserMySQL(
            id="user-1",
            username="user-1",
            password_hash="",
            role="admin",
            must_change_password=False,
        )
        policy = resolve_access_policy(user, domain="audio", datasource="audio_dw")
        set_request_id("trace-new-branch")

        events = [
            event
            async for event in service.events(
                "换成月粒度",
                conversation_id="conversation-branch",
                parent_trace_id="trace-root",
                regenerate_of_trace_id="trace-source",
                user_id="user-1",
                access_policy=policy,
            )
        ]

        assert events[-1]["status"] == "completed"
        source = await session.get(QueryTraceMySQL, "trace-source")
        branch = await session.get(QueryTraceMySQL, "trace-new-branch")
        assert source.answer_summary == "原始结果"
        assert source.policy_hash == "old-policy-hash"
        assert branch.parent_trace_id == "trace-root"
        assert branch.regenerate_of_trace_id == "trace-source"
        assert branch.conversation_id == "conversation-branch"
        assert branch.standalone_question == "最近三个月播放趋势，按月"
        assert graph_runner.kwargs["input"]["query"] == branch.standalone_question
        assert graph_runner.kwargs["input"]["access_policy"]["policy_hash"] == policy.policy_hash
        assert "sql" not in graph_runner.kwargs["input"]
        assert "forbidden_legacy_table" not in str(graph_runner.kwargs["input"])
        assert branch.policy_hash == policy.policy_hash

        set_request_id("trace-needs-input")
        clarification_events = [
            event
            async for event in service.events(
                "那上个月呢",
                conversation_id="conversation-branch",
                parent_trace_id=None,
                user_id="user-1",
                access_policy=policy,
            )
        ]
        assert [event["type"] for event in clarification_events] == [
            "context",
            "clarification",
            "done",
        ]
        assert clarification_events[-1]["status"] == "needs_input"
        assert graph_runner.calls == 1
        clarification_trace = await session.get(QueryTraceMySQL, "trace-needs-input")
        assert clarification_trace.status == "needs_input"
        assert clarification_trace.sql is None

        set_request_id("trace-needs-input-sync")
        clarification_result = await service.query_sync(
            "还有呢",
            conversation_id="conversation-branch",
            parent_trace_id="trace-root",
            user_id="user-1",
            access_policy=policy,
        )
        assert clarification_result["status"] == "needs_input"
        assert clarification_result["clarification"]
        assert clarification_result["context_resolution_confidence"] == "low"
        assert graph_runner.calls == 1

        with pytest.raises(ConversationContextError, match="无权访问"):
            await service.validate_conversation_context(
                user_id="user-1",
                conversation_id="conversation-other",
                parent_trace_id=None,
            )
        with pytest.raises(ConversationContextError, match="必须指定会话"):
            await service.validate_conversation_context(
                user_id="user-1",
                conversation_id=None,
                parent_trace_id="trace-root",
            )

    asyncio.run(_with_session(scenario))
