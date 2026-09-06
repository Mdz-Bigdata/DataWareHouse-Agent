"""Owner-scoped, re-authorized analysis of an existing query trace."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.conf.app_config import app_config
from app.repositories.dialect import get_dialect_strategy
from app.services.access_policy import AccessPolicyContextV1
from app.services.explain_budget_service import enforce_explain_budget
from app.services.sql_guard import (
    extract_filter_only_columns,
    extract_sensitive_columns,
    validate_and_normalize_sql,
)

ANALYSIS_ROW_LIMIT = 100
ANALYSIS_TIMEOUT_SECONDS = 10


class DeepAnalysisService:
    """Re-run one owned SQL trace under today's policy and semantic build."""

    def __init__(self, dw_repository, meta_repository, trace_repository):
        self.dw_repository = dw_repository
        self.meta_repository = meta_repository
        self.trace_repository = trace_repository

    async def analyze(
        self,
        *,
        source_trace_id: str,
        user_id: str,
        access_policy: AccessPolicyContextV1,
    ) -> dict[str, Any]:
        source = await self.trace_repository.get_for_user(source_trace_id, user_id)
        if source is None:
            raise LookupError("查询记录不存在")
        if source.status != "completed" or not source.sql:
            raise ValueError("只有已完成且包含 SQL 的查询才能深入分析")

        trace_id = str(uuid.uuid4())
        started_at = time.perf_counter()
        await self.trace_repository.create_trace(
            trace_id,
            f"深入分析：{source.query_text}",
            user_id,
            policy_version=access_policy.policy_version,
            policy_hash=access_policy.policy_hash,
            policy_admin_bypass=access_policy.admin_bypass,
            conversation_id=source.conversation_id,
            parent_trace_id=source.id,
            standalone_question=f"深入分析：{source.standalone_question or source.query_text}",
        )
        try:
            await self._record_phase(
                trace_id,
                1,
                "重新鉴权",
                "success",
                started_at,
            )
            build_id, table_infos, relationships = await self._current_catalog(
                access_policy.domain
            )
            dialect = get_dialect_strategy(app_config.db_dw.dialect).sqlglot_dialect
            validation_started = time.perf_counter()
            safe_sql = validate_and_normalize_sql(
                source.sql,
                table_infos,
                ANALYSIS_ROW_LIMIT,
                sensitive_columns=extract_sensitive_columns(table_infos),
                filter_only_columns=extract_filter_only_columns(table_infos),
                relationships=relationships,
                row_level_scope=access_policy.row_level_scope(),
                dialect=dialect,
                table_acl=access_policy.table_acl,
                allowed_functions=access_policy.function_whitelist,
            )
            estimate = await self.dw_repository.validate_sql(
                safe_sql.sql,
                min(ANALYSIS_TIMEOUT_SECONDS, app_config.query.timeout_seconds),
            )
            if estimate is not None:
                enforce_explain_budget(
                    estimate,
                    max_cost=app_config.query.explain_cost_budget,
                    max_rows=app_config.query.explain_rows_budget,
                )
            await self._record_phase(
                trace_id,
                2,
                "校验原SQL",
                "success",
                validation_started,
                sql=safe_sql.sql,
            )

            execution_started = time.perf_counter()
            rows = await self.dw_repository.execute_sql(
                safe_sql.sql,
                min(ANALYSIS_TIMEOUT_SECONDS, app_config.query.timeout_seconds),
            )
            await self._record_phase(
                trace_id,
                3,
                "限量重跑原SQL",
                "success",
                execution_started,
                sql=safe_sql.sql,
            )
            report = _build_report(rows)
            result = {
                "trace_id": trace_id,
                "source_trace_id": source.id,
                "status": "completed",
                "facts": report["facts"],
                "inferences": report["inferences"],
                "evidence": report["evidence"],
                "rerun_row_count": len(rows),
                "row_limit": ANALYSIS_ROW_LIMIT,
                "truncated": safe_sql.limit == ANALYSIS_ROW_LIMIT
                and len(rows) == ANALYSIS_ROW_LIMIT,
                "policy_version": access_policy.policy_version,
                "policy_hash": access_policy.policy_hash,
                "build_id": build_id,
                "disclaimer": "推断仅基于本次重新鉴权后的有限结果，不包含未来预测。",
            }
            await self.trace_repository.finish_trace(
                trace_id=trace_id,
                status="completed",
                total_duration_ms=_elapsed_ms(started_at),
                sql=safe_sql.sql,
                build_id=build_id,
                standalone_question=f"深入分析：{source.standalone_question or source.query_text}",
                query_plan_summary={
                    "schema_version": "deep-analysis/v1",
                    "source_trace_id": source.id,
                    "row_limit": ANALYSIS_ROW_LIMIT,
                },
                answer_summary="；".join(
                    str(item["statement"]) for item in report["facts"][:5]
                ),
                chart_spec=None,
            )
            return result
        except Exception as exc:
            await self._record_phase(
                trace_id,
                4,
                "深入分析",
                "error",
                started_at,
                error_message=str(exc),
            )
            await self.trace_repository.finish_trace(
                trace_id=trace_id,
                status="failed",
                total_duration_ms=_elapsed_ms(started_at),
                error_message=str(exc),
                query_plan_summary={
                    "schema_version": "deep-analysis/v1",
                    "source_trace_id": source.id,
                    "row_limit": ANALYSIS_ROW_LIMIT,
                },
            )
            raise

    async def _current_catalog(self, domain: str) -> tuple[str, list[dict], list[dict]]:
        build_id = await self.meta_repository.get_active_build_id(domain)
        if build_id is None:
            raise LookupError("当前没有可用的语义构建")
        tables = await self.meta_repository.list_table_infos(build_id)
        columns = await self.meta_repository.list_allowed_column_infos(build_id)
        columns_by_table: dict[str, list[dict]] = {}
        for column in columns:
            columns_by_table.setdefault(column.table_id, []).append(
                {**asdict(column), "filter_only": False}
            )
        table_infos = [
            {**asdict(table), "columns": columns_by_table.get(table.id, [])}
            for table in tables
        ]
        relationships = [
            asdict(item)
            for item in await self.meta_repository.get_all_relationships(build_id)
        ]
        return build_id, table_infos, relationships

    async def _record_phase(
        self,
        trace_id: str,
        sequence: int,
        step: str,
        status: str,
        started_at: float,
        *,
        sql: str | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.trace_repository.record_phase(
            trace_id=trace_id,
            sequence=sequence,
            step=step,
            status=status,
            duration_ms=_elapsed_ms(started_at),
            sql=sql,
            error_message=error_message,
        )


def _build_report(rows: list[dict[str, Any]]) -> dict[str, list[dict]]:
    columns = list(rows[0].keys()) if rows else []
    evidence: list[dict] = [
        {
            "evidence_id": "evidence-shape",
            "description": "重新鉴权后的结果形状",
            "values": {"row_count": len(rows), "column_count": len(columns)},
        }
    ]
    facts: list[dict] = [
        {
            "fact_id": "fact-shape",
            "statement": f"本次重新鉴权后返回 {len(rows)} 行、{len(columns)} 列。",
            "evidence_ids": ["evidence-shape"],
        }
    ]
    inferences: list[dict] = []
    if not rows:
        return {"facts": facts, "inferences": inferences, "evidence": evidence}

    numeric_summaries: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for column in columns:
        values = [_as_decimal(row.get(column)) for row in rows]
        numeric = [value for value in values if value is not None]
        if not numeric:
            continue
        minimum = min(numeric)
        maximum = max(numeric)
        average = sum(numeric, Decimal(0)) / len(numeric)
        numeric_summaries.append((column, minimum, maximum, average))
        evidence_id = f"evidence-numeric-{len(numeric_summaries)}"
        fact_id = f"fact-numeric-{len(numeric_summaries)}"
        evidence.append(
            {
                "evidence_id": evidence_id,
                "description": f"{column} 的有限结果统计",
                "values": {
                    "minimum": _format_decimal(minimum),
                    "maximum": _format_decimal(maximum),
                    "average": _format_decimal(average),
                    "observations": len(numeric),
                },
            }
        )
        facts.append(
            {
                "fact_id": fact_id,
                "statement": (
                    f"{column} 在本次结果中的最小值为 {_format_decimal(minimum)}，"
                    f"最大值为 {_format_decimal(maximum)}，平均值为 {_format_decimal(average)}。"
                ),
                "evidence_ids": [evidence_id],
            }
        )
        if len(numeric_summaries) >= 3:
            break

    dimension = next(
        (
            column
            for column in columns
            if all(_as_decimal(row.get(column)) is None for row in rows)
        ),
        None,
    )
    if dimension and numeric_summaries:
        metric = numeric_summaries[0][0]
        ranked = [
            (row, _as_decimal(row.get(metric)))
            for row in rows
            if _as_decimal(row.get(metric)) is not None
        ]
        if ranked:
            top_row, top_value = max(ranked, key=lambda item: item[1] or Decimal(0))
            evidence.append(
                {
                    "evidence_id": "evidence-top",
                    "description": "本次有限结果中的最大项",
                    "values": {
                        "dimension": dimension,
                        "dimension_value": _json_value(top_row.get(dimension)),
                        "metric": metric,
                        "metric_value": _format_decimal(top_value or Decimal(0)),
                    },
                }
            )
            facts.append(
                {
                    "fact_id": "fact-top",
                    "statement": (
                        f"本次结果中 {dimension}={_json_value(top_row.get(dimension))} 的"
                        f" {metric} 最大，为 {_format_decimal(top_value or Decimal(0))}。"
                    ),
                    "evidence_ids": ["evidence-top"],
                }
            )

    for index, (column, minimum, maximum, _average) in enumerate(numeric_summaries, 1):
        if minimum > 0 and maximum / minimum >= Decimal(2):
            inferences.append(
                {
                    "inference_id": f"inference-spread-{index}",
                    "statement": (
                        f"在本次有限结果内，{column} 的最大值至少是最小值的 2 倍，"
                        "各项之间存在明显差异。"
                    ),
                    "fact_ids": [f"fact-numeric-{index}"],
                    "confidence": "medium",
                }
            )
    return {"facts": facts, "inferences": inferences, "evidence": evidence}


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _format_decimal(value: Decimal) -> str:
    text = format(round(value, 4), "f")
    return text.rstrip("0").rstrip(".") or "0"


def _json_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return _format_decimal(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
