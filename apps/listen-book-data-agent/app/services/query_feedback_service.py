from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import sqlglot

from app.models.mysql.query_feedback_mysql import QueryFeedbackMySQL
from app.repositories.mysql.query_feedback_repository import QueryFeedbackRepository
from app.repositories.mysql.verified_query_repository import VerifiedQueryRepository
from app.services.sql_template_service import (
    ParameterizedSQLTemplate,
    build_parameterized_sql_template,
    redact_feedback_text,
)
from app.services.verified_query_service import VerifiedQueryService

_REASONS = {
    "correct": {"accurate", "clear", "helpful", "other"},
    "incorrect": {
        "wrong_metric",
        "wrong_filter",
        "wrong_join",
        "wrong_time_range",
        "wrong_granularity",
        "missing_data",
        "other",
    },
}


@dataclass(frozen=True)
class QueryFeedbackResult:
    id: str
    trace_id: str
    verdict: str
    reasons: list[str]
    comment: str
    template_signature: str
    candidate_revision_id: str | None
    positive_count: int | None


class QueryFeedbackService:
    def __init__(
        self,
        feedback_repository: QueryFeedbackRepository,
        verified_repository: VerifiedQueryRepository,
    ):
        self.feedback_repository = feedback_repository
        self.verified_repository = verified_repository

    async def submit(
        self,
        *,
        trace_id: str,
        user_id: str,
        verdict: str,
        reasons: list[str],
        comment: str,
        domain: str,
        datasource: str,
        row_level_scope: list[dict] | None,
        dialect: str = "mysql",
    ) -> QueryFeedbackResult:
        normalized_reasons = _validate_feedback(verdict, reasons)
        trace = await self.feedback_repository.get_owned_trace(trace_id, user_id)
        if trace is None:
            raise LookupError("查询记录不存在")
        if trace.status != "completed" or not trace.sql:
            raise ValueError("只能评价已完成且包含 SQL 的查询")
        if await self.feedback_repository.get_for_trace(trace_id) is not None:
            raise ValueError("该查询已经提交过反馈")

        template = build_parameterized_sql_template(
            trace.sql,
            row_level_scope=row_level_scope,
            dialect=dialect,
        )
        if not template.sql or template.sql.startswith("/* redacted:"):
            raise ValueError("当前查询无法生成安全反馈模板")
        signature = _template_signature(
            template,
            domain=domain,
            datasource=datasource,
            dialect=dialect,
        )
        candidate_revision_id: str | None = None
        positive_count: int | None = None
        try:
            if verdict == "incorrect":
                candidate = await self._create_candidate(
                    trace=trace,
                    template=template,
                    signature=signature,
                    domain=domain,
                    datasource=datasource,
                    dialect=dialect,
                    user_id=user_id,
                )
                candidate_revision_id = candidate.id
            else:
                confidence = await self.feedback_repository.increment_positive_confidence(
                    template_signature=signature,
                    domain=domain,
                    datasource=datasource,
                    sql_template=template.sql,
                    parameter_types=template.parameter_types,
                    trace_id=trace_id,
                )
                positive_count = confidence.positive_count

            feedback = QueryFeedbackMySQL(
                id=str(uuid.uuid4()),
                trace_id=trace_id,
                user_id=user_id,
                verdict=verdict,
                reasons=normalized_reasons,
                comment=redact_feedback_text(comment, max_length=1000),
                template_signature=signature,
                candidate_revision_id=candidate_revision_id,
            )
            await self.feedback_repository.add_feedback(feedback)
            await self.feedback_repository.session.commit()
        except Exception:
            await self.feedback_repository.session.rollback()
            raise
        return QueryFeedbackResult(
            id=feedback.id,
            trace_id=trace_id,
            verdict=verdict,
            reasons=normalized_reasons,
            comment=feedback.comment,
            template_signature=signature,
            candidate_revision_id=candidate_revision_id,
            positive_count=positive_count,
        )

    async def _create_candidate(
        self,
        *,
        trace,
        template: ParameterizedSQLTemplate,
        signature: str,
        domain: str,
        datasource: str,
        dialect: str,
        user_id: str,
    ):
        expected_fields = _projected_fields(template.sql, dialect=dialect)
        parameter_schema = [
            {"name": f"p{index}", "type": parameter_type, "required": True}
            for index, parameter_type in enumerate(template.parameter_types, 1)
        ]
        return await VerifiedQueryService(self.verified_repository).create_revision(
            case_key=f"feedback_{signature[:16]}",
            question=redact_feedback_text(trace.query_text, max_length=500),
            dialect=dialect,
            sql_template=template.sql,
            parameter_schema=parameter_schema,
            expected_fields=expected_fields,
            expected_metrics=[],
            assertions=[],
            domain=domain,
            datasource=datasource,
            source_trace_id=trace.id,
            source="feedback",
            created_by=user_id,
            commit=False,
        )


def _validate_feedback(verdict: str, reasons: list[str]) -> list[str]:
    if verdict not in _REASONS:
        raise ValueError("反馈结论无效")
    normalized = list(dict.fromkeys(str(reason).strip() for reason in reasons))
    if not normalized or any(reason not in _REASONS[verdict] for reason in normalized):
        raise ValueError("反馈原因与结论不匹配")
    return normalized


def _template_signature(
    template: ParameterizedSQLTemplate,
    *,
    domain: str,
    datasource: str,
    dialect: str,
) -> str:
    value = "\0".join(
        (
            domain,
            datasource,
            dialect,
            template.sql,
            ",".join(template.parameter_types),
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _projected_fields(sql: str, *, dialect: str) -> list[str]:
    expression = sqlglot.parse_one(sql, read=dialect)
    fields = [item.alias_or_name for item in expression.expressions]
    if not fields or any(not field for field in fields):
        raise ValueError("错误反馈 SQL 缺少可审核的输出字段")
    return fields
