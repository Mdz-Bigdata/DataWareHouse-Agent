"""Deterministic Selector, Decomposer and Refiner roles for complex plans."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


class ComplexPlanError(ValueError):
    """Raised when a complex plan references unavailable semantic objects."""


@dataclass(frozen=True)
class SemanticSelection:
    metric_ids: tuple[str, ...]
    field_ids: tuple[str, ...]
    table_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]

    def to_state(self) -> dict[str, list[str]]:
        return {
            "metric_ids": list(self.metric_ids),
            "field_ids": list(self.field_ids),
            "table_ids": list(self.table_ids),
            "relationship_ids": list(self.relationship_ids),
        }


def select_query_semantics(
    query_plan: dict[str, Any],
    *,
    metric_infos: list[dict[str, Any]],
    table_infos: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> SemanticSelection:
    """Select exact semantic IDs without modifying the recalled safe context."""

    available_metrics = {str(item.get("id")) for item in metric_infos if item.get("id")}
    available_fields = {
        str(column.get("id"))
        for table in table_infos
        for column in table.get("columns", [])
        if column.get("id")
    }
    available_relationships = {
        str(item.get("id")): item for item in relationships if item.get("id")
    }
    metric_ids = tuple(
        dict.fromkeys(
            str(item.get("semantic_id"))
            for item in query_plan.get("metrics", [])
            if str(item.get("semantic_id")) in available_metrics
        )
    )
    requested_fields = [
        str(item.get("semantic_id")) for item in query_plan.get("dimensions", [])
    ]
    requested_fields.extend(
        str(field_id)
        for item in query_plan.get("filters", [])
        for field_id in item.get("field_ids", [])
    )
    time_field = (query_plan.get("time") or {}).get("field_id")
    if time_field:
        requested_fields.append(str(time_field))
    field_ids = tuple(
        dict.fromkeys(field_id for field_id in requested_fields if field_id in available_fields)
    )
    relationship_ids = tuple(
        dict.fromkeys(
            str(relationship_id)
            for relationship_id in query_plan.get("join_path", [])
            if str(relationship_id) in available_relationships
        )
    )
    table_ids = [field_id.rsplit(".", 1)[0] for field_id in field_ids]
    selected_metrics = {
        str(item.get("id")): item for item in metric_infos if str(item.get("id")) in metric_ids
    }
    table_ids.extend(
        str(column_id).rsplit(".", 1)[0]
        for metric in selected_metrics.values()
        for column_id in metric.get("relevant_columns", [])
        if "." in str(column_id)
    )
    for relationship_id in relationship_ids:
        relationship = available_relationships[relationship_id]
        table_ids.extend(
            [str(relationship.get("source_table")), str(relationship.get("target_table"))]
        )
    available_tables = {
        str(table.get("id") or table.get("name")) for table in table_infos
    }
    return SemanticSelection(
        metric_ids=metric_ids,
        field_ids=field_ids,
        table_ids=tuple(
            dict.fromkeys(table_id for table_id in table_ids if table_id in available_tables)
        ),
        relationship_ids=relationship_ids,
    )


def decompose_nested_plan(query_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize bounded subplans from QueryPlanV1 without generating SQL."""

    if query_plan.get("complexity") != "NESTED":
        return []
    subplans = query_plan.get("subplans", [])
    if not subplans:
        raise ComplexPlanError("NESTED 查询缺少可执行子计划")
    if len(subplans) > 6:
        raise ComplexPlanError("复杂查询子计划不能超过 6 个")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(subplans):
        subplan_id = str(item.get("subplan_id") or "")
        if not subplan_id:
            raise ComplexPlanError(f"第 {index + 1} 个子计划缺少稳定 ID")
        result.append(
            {
                "subplan_id": subplan_id,
                "purpose": str(item.get("purpose") or "nested_aggregation"),
                "metric_ids": list(dict.fromkeys(item.get("metric_ids", []))),
                "dimension_ids": list(dict.fromkeys(item.get("dimension_ids", []))),
                "filter_ids": list(dict.fromkeys(item.get("filter_ids", []))),
                "depends_on": list(dict.fromkeys(item.get("depends_on", []))),
            }
        )
    return result


def refine_complex_plan(
    query_plan: dict[str, Any],
    selection: dict[str, Any],
    decomposition: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed when selected IDs and subplans disagree with QueryPlanV1."""

    if query_plan.get("schema_version") != "query-plan/v1":
        raise ComplexPlanError("复杂查询必须使用 query-plan/v1")
    if query_plan.get("complexity") == "EASY":
        raise ComplexPlanError("EASY 查询不应进入复杂计划 Refiner")

    plan = deepcopy(query_plan)
    plan_metrics = {
        str(item.get("semantic_id")) for item in plan.get("metrics", []) if item.get("semantic_id")
    }
    plan_fields = {
        str(item.get("semantic_id"))
        for item in plan.get("dimensions", [])
        if item.get("semantic_id")
    }
    plan_fields.update(
        str(field_id)
        for item in plan.get("filters", [])
        for field_id in item.get("field_ids", [])
    )
    time_field = (plan.get("time") or {}).get("field_id")
    if time_field:
        plan_fields.add(str(time_field))
    plan_relationships = {str(value) for value in plan.get("join_path", [])}
    _require_subset("指标", selection.get("metric_ids", []), plan_metrics)
    _require_subset("字段", selection.get("field_ids", []), plan_fields)
    _require_subset("关系", selection.get("relationship_ids", []), plan_relationships)

    filter_ids = {
        str(item.get("filter_id")) for item in plan.get("filters", []) if item.get("filter_id")
    }
    if plan.get("complexity") == "NESTED":
        if not decomposition:
            raise ComplexPlanError("NESTED 查询未生成分解计划")
        subplan_ids: set[str] = set()
        for item in decomposition:
            subplan_id = str(item.get("subplan_id") or "")
            if not subplan_id or subplan_id in subplan_ids:
                raise ComplexPlanError("子计划 ID 缺失或重复")
            subplan_ids.add(subplan_id)
            _require_subset("子计划指标", item.get("metric_ids", []), plan_metrics)
            _require_subset("子计划维度", item.get("dimension_ids", []), plan_fields)
            _require_subset("子计划筛选", item.get("filter_ids", []), filter_ids)
    plan["refinement"] = {
        "status": "validated",
        "selected_metric_count": len(selection.get("metric_ids", [])),
        "selected_field_count": len(selection.get("field_ids", [])),
        "subplan_count": len(decomposition),
    }
    return plan


def _require_subset(label: str, values: Any, allowed: set[str]) -> None:
    unknown = {str(value) for value in values} - allowed
    if unknown:
        raise ComplexPlanError(f"{label}不属于当前 QueryPlan：{', '.join(sorted(unknown))}")
