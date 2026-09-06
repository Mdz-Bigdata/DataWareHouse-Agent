"""Fail-closed validation for QueryPlanV1 before any SQL is produced."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class DryPlanValidationError(ValueError):
    """Raised when a plan cannot be grounded in the current request context."""


@dataclass(frozen=True)
class DryPlanResult:
    checks: tuple[str, ...]


def validate_dry_plan(
    query_plan: dict[str, Any],
    *,
    metric_infos: list[dict[str, Any]],
    table_infos: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    access_policy: dict[str, Any],
    max_result_rows: int,
) -> DryPlanResult:
    if query_plan.get("schema_version") != "query-plan/v1":
        raise DryPlanValidationError("SQL 生成前必须提供 query-plan/v1")
    if query_plan.get("intent") not in {"aggregate", "trend", "ranking", "compare", "detail"}:
        raise DryPlanValidationError("QueryPlan 意图无效")
    if query_plan.get("complexity") not in {"EASY", "NON_NESTED", "NESTED"}:
        raise DryPlanValidationError("QueryPlan 复杂度无效")

    metric_ids = {str(item.get("id")) for item in metric_infos if item.get("id")}
    plan_metric_ids = {
        str(item.get("semantic_id"))
        for item in query_plan.get("metrics", [])
        if item.get("semantic_id")
    }
    _require_subset("指标", plan_metric_ids, metric_ids)

    field_ids = {
        str(column.get("id"))
        for table in table_infos
        for column in table.get("columns", [])
        if column.get("id")
    }
    plan_field_ids = {
        str(item.get("semantic_id"))
        for item in query_plan.get("dimensions", [])
        if item.get("semantic_id")
    }
    for item in query_plan.get("filters", []):
        fields = {str(value) for value in item.get("field_ids", [])}
        if not fields:
            raise DryPlanValidationError("QueryPlan 筛选器缺少稳定字段 ID")
        plan_field_ids.update(fields)
    time_field = (query_plan.get("time") or {}).get("field_id")
    if time_field:
        plan_field_ids.add(str(time_field))
    _require_subset("字段", plan_field_ids, field_ids)

    relationship_ids = {
        str(item.get("id")) for item in relationships if item.get("id")
    }
    plan_relationship_ids = {str(value) for value in query_plan.get("join_path", [])}
    _require_subset("Join Path", plan_relationship_ids, relationship_ids)

    valid_sort_ids = plan_metric_ids | {
        str(item.get("semantic_id"))
        for item in query_plan.get("dimensions", [])
        if item.get("semantic_id")
    }
    sort_ids = {
        str(item.get("semantic_id"))
        for item in query_plan.get("sort", [])
        if item.get("semantic_id")
    }
    _require_subset("排序", sort_ids, valid_sort_ids)

    limit = query_plan.get("limit")
    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= max_result_rows):
        raise DryPlanValidationError(f"QueryPlan limit 必须在 1 到 {max_result_rows} 之间")

    referenced_tables = {field_id.rsplit(".", 1)[0] for field_id in plan_field_ids}
    referenced_tables.update(
        str(column_id).rsplit(".", 1)[0]
        for metric in metric_infos
        if str(metric.get("id")) in plan_metric_ids
        for column_id in metric.get("relevant_columns", [])
        if "." in str(column_id)
    )
    _validate_table_acl(referenced_tables, access_policy)
    return DryPlanResult(
        checks=(
            "schema_version",
            "semantic_references",
            "join_path",
            "sort_and_limit",
            "table_acl",
        )
    )


def _require_subset(label: str, values: set[str], allowed: set[str]) -> None:
    unknown = values - allowed
    if unknown:
        raise DryPlanValidationError(f"QueryPlan {label}未在当前语义构建中召回：{', '.join(sorted(unknown))}")


def _validate_table_acl(tables: set[str], access_policy: dict[str, Any]) -> None:
    if access_policy.get("admin_bypass"):
        return
    acl = {str(value) for value in (access_policy.get("table_acl") or {})}
    if not acl or "*" in acl:
        return
    unknown = tables - acl
    if unknown:
        raise DryPlanValidationError(
            "QueryPlan 引用了当前策略未授权的表：" + ", ".join(sorted(unknown))
        )
