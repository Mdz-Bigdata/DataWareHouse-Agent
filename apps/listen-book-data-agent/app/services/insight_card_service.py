from __future__ import annotations

import uuid

from app.models.mysql.insight_card_mysql import InsightCardMySQL
from app.repositories.mysql.insight_card_repository import InsightCardRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.sql_template_service import build_parameterized_sql_template


class InsightCardService:
    """Save only bounded insight metadata and a redacted parameterized SQL template."""

    def __init__(
        self,
        card_repository: InsightCardRepository,
        trace_repository: QueryTraceRepository,
    ):
        self.card_repository = card_repository
        self.trace_repository = trace_repository

    async def save_from_trace(
        self,
        *,
        trace_id: str,
        user_id: str,
        row_level_scope: list[dict],
        dialect: str,
    ) -> InsightCardMySQL:
        trace = await self.trace_repository.get_for_user(trace_id, user_id)
        if trace is None:
            raise LookupError("查询记录不存在")
        if trace.status != "completed" or not trace.sql or not trace.answer_summary:
            raise ValueError("只有已完成且包含答案与 SQL 的查询才能保存洞察")
        if not isinstance(trace.chart_spec, dict):
            raise ValueError("当前查询缺少可保存的 ChartSpec")

        template = build_parameterized_sql_template(
            trace.sql,
            row_level_scope=row_level_scope,
            dialect=dialect,
        )
        if not template.sql or template.sql.startswith("/* redacted:"):
            raise ValueError("当前查询无法生成安全的参数化 SQL")
        card = InsightCardMySQL(
            id=str(uuid.uuid4()),
            user_id=user_id,
            question=trace.standalone_question or trace.query_text,
            answer_summary=trace.answer_summary,
            sql_template=template.sql,
            parameter_types=list(template.parameter_types),
            chart_spec=dict(trace.chart_spec),
            version_info={
                "schema_version": "insight-card-versions/v1",
                "build_id": trace.build_id,
                "semantic_release_id": trace.semantic_release_id,
                "semantic_release_version": trace.semantic_release_version,
                "query_set_id": trace.query_set_id,
                "query_set_version": trace.query_set_version,
                "business_rule_set_id": trace.business_rule_set_id,
                "business_rule_set_version": trace.business_rule_set_version,
                "policy_version": trace.policy_version,
                "policy_hash": trace.policy_hash,
            },
        )
        try:
            await self.card_repository.add(card)
            await self.card_repository.session.commit()
            await self.card_repository.session.refresh(card)
        except Exception:
            await self.card_repository.session.rollback()
            raise
        return card

    async def get_owned(self, card_id: str, user_id: str) -> InsightCardMySQL:
        card = await self.card_repository.get_for_user(card_id, user_id)
        if card is None:
            raise LookupError("洞察卡片不存在")
        return card
