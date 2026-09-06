from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.analysis_plan import build_analysis_plan
from app.models.mysql.base import Base
from app.models.mysql.conversation_mysql import ConversationMySQL
from app.repositories.mysql.conversation_repository import ConversationRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.conversation_context_service import (
    ConversationTurnContext,
    resolve_conversation_context,
    resolve_standalone_question,
)


def _turn(trace_id: str, question: str) -> ConversationTurnContext:
    return ConversationTurnContext(
        trace_id=trace_id,
        standalone_question=question,
        query_plan=build_analysis_plan(question).to_state(),
        verified_sql_template="SELECT COUNT(*) FROM play_session",
        answer_summary="播放次数为 100。",
    )


def test_context_resolves_relative_time_filter_and_dimension_modifiers():
    turns = (_turn("trace-nearest", "最近三个月按月播放次数趋势"),)

    relative_time = resolve_standalone_question("那上个月呢", turns)
    assert "最近三个月" not in relative_time.standalone_question
    assert "上个月" in relative_time.standalone_question
    assert "播放次数" in relative_time.standalone_question

    region = resolve_standalone_question("只看广东", turns)
    assert "播放次数" in region.standalone_question
    assert "广东地区" in region.standalone_question

    dimension = resolve_standalone_question("按渠道拆开", turns)
    assert "播放次数" in dimension.standalone_question
    assert "按渠道拆分" in dimension.standalone_question
    assert dimension.used_trace_ids == ("trace-nearest",)


def test_explicit_no_inherit_has_priority_over_history():
    result = resolve_standalone_question(
        "不要继承之前条件，查询订单数",
        (_turn("trace-nearest", "最近三个月按月播放次数趋势"),),
    )

    assert result.standalone_question == "查询订单数"
    assert result.inherited is False
    assert result.used_trace_ids == ()


def test_context_without_successful_history_reports_low_confidence():
    result = resolve_standalone_question("那上个月呢", ())

    assert result.standalone_question == "那上个月呢"
    assert result.confidence == "low"
    assert result.ambiguity_reason == "缺少可用的成功祖先轮次"

    vague = resolve_standalone_question(
        "还有呢",
        (_turn("trace-nearest", "最近三个月按月播放次数趋势"),),
    )
    assert vague.confidence == "low"
    assert vague.inherited is False

    reset_only = resolve_standalone_question(
        "不要继承之前条件",
        (_turn("trace-nearest", "最近三个月按月播放次数趋势"),),
    )
    assert reset_only.confidence == "low"


def test_context_uses_only_three_successful_owned_ancestors_and_redacts_sql():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            conversations = ConversationRepository(session)
            traces = QueryTraceRepository(session)
            await conversations.add(
                ConversationMySQL(
                    id="conversation-context",
                    user_id="user-1",
                    title="上下文裁剪",
                    status="active",
                )
            )
            await session.commit()
            parent_id = None
            statuses = ("completed", "completed", "failed", "completed", "completed")
            for index, trace_status in enumerate(statuses, 1):
                trace_id = f"trace-{index}"
                await traces.create_trace(
                    trace_id,
                    f"第{index}轮播放次数",
                    "user-1",
                    conversation_id="conversation-context",
                    parent_trace_id=parent_id,
                    standalone_question=f"第{index}轮播放次数",
                )
                await traces.finish_trace(
                    trace_id=trace_id,
                    status=trace_status,
                    total_duration_ms=10,
                    sql=(
                        "SELECT COUNT(*) FROM play_session p "
                        "WHERE p.region = '华东' AND p.play_status = 'completed'"
                    ),
                    query_plan_summary={"intent": "aggregate"},
                    answer_summary=f"第{index}轮答案",
                )
                parent_id = trace_id

            result = await resolve_conversation_context(
                query="那上个月呢",
                conversation_id="conversation-context",
                parent_trace_id="trace-5",
                regenerate_of_trace_id=None,
                user_id="user-1",
                repository=traces,
                row_level_scope=[
                    {"table": "play_session", "column": "region", "value": "华东"}
                ],
                dialect="mysql",
            )

            assert result.used_trace_ids == ("trace-5", "trace-4", "trace-2")
            assert len(result.turns) == 3
            assert "华东" not in result.turns[0].verified_sql_template
            assert "completed" not in result.turns[0].verified_sql_template
            assert ":p1" in result.turns[0].verified_sql_template
            other_user = await traces.list_successful_ancestors_for_user(
                conversation_id="conversation-context",
                parent_trace_id="trace-5",
                user_id="user-2",
            )
            assert other_user == []
        await engine.dispose()

    asyncio.run(scenario())
