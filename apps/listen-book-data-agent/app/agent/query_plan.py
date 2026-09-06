"""Versioned semantic query plans resolved against the active metadata build."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class QueryComplexity(StrEnum):
    EASY = "EASY"
    NON_NESTED = "NON_NESTED"
    NESTED = "NESTED"


@dataclass(frozen=True)
class SemanticRefV1:
    semantic_id: str
    label: str


@dataclass(frozen=True)
class QueryFilterV1:
    filter_id: str
    field_ids: tuple[str, ...]
    operator: str
    values: tuple[str, ...]
    label: str
    location: str = "where"
    filter_only: bool = False


@dataclass(frozen=True)
class QueryTimeV1:
    field_id: str | None
    start: str | None
    end: str | None
    label: str | None
    grain: str | None


@dataclass(frozen=True)
class QuerySortV1:
    semantic_id: str
    direction: str


@dataclass(frozen=True)
class QuerySubplanV1:
    subplan_id: str
    purpose: str
    metric_ids: tuple[str, ...] = ()
    dimension_ids: tuple[str, ...] = ()
    filter_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryPlanV1:
    """Stable, JSON-serializable contract consumed by planning and audit layers."""

    schema_version: str
    intent: str
    complexity: QueryComplexity
    metrics: tuple[SemanticRefV1, ...]
    dimensions: tuple[SemanticRefV1, ...]
    filters: tuple[QueryFilterV1, ...]
    time: QueryTimeV1
    sort: tuple[QuerySortV1, ...]
    join_path: tuple[str, ...]
    subplans: tuple[QuerySubplanV1, ...]
    limit: int | None
    comparison: str | None
    source_hints: dict[str, Any] = field(default_factory=dict)

    def to_state(self) -> dict[str, Any]:
        value = asdict(self)
        value["complexity"] = self.complexity.value
        return value


_METRIC_HINT_IDS = {
    "播放完成率": "play_completion_rate",
    "完播率": "play_completion_rate",
    "播放次数": "play_count",
    "播放量": "play_count",
    "平均播放时长": "average_played_seconds",
    "订单金额": "paid_content_order_amount",
    "订单数": "paid_content_order_count",
    "退款金额": "successful_refund_amount",
    "收藏数": "favorite_count",
    "评论数": "approved_comment_count",
    "评分": "average_rating",
    "会员数": "active_member_count",
    "搜索量": "search_count",
    "点击率": "search_click_rate",
}

_DIMENSION_SUFFIXES = {
    "专辑": ("album_id", "album_name", "album_title"),
    "声音": ("track_id", "track_name", "track_title"),
    "章节": ("track_id", "track_name", "track_title"),
    "作者": ("author_id", "author_name"),
    "主播": ("narrator_id", "narrator_name"),
    "分类": ("category_id", "category_name"),
    "地区": ("province", "city", "region"),
    "会员": ("member_level", "user_id"),
    "订单": ("order_id", "order_type"),
    "渠道": ("channel_id", "payment_channel"),
    "设备": ("device_id", "device_type"),
    "关键词": ("keyword",),
    "榜单": ("ranking_id", "rank_no"),
}

_DIMENSION_HINT_KEYS = dict(
    zip(
        _DIMENSION_SUFFIXES,
        (
            "album",
            "track",
            "track",
            "author",
            "narrator",
            "category",
            "region",
            "member",
            "order",
            "channel",
            "device",
            "keyword",
            "ranking",
        ),
        strict=True,
    )
)

_NESTED_PATTERNS = (
    r"占比",
    r"留存",
    r"复购",
    r"每(?:个|位|类).*(?:最高|最低|前\s*\d+)",
    r"先.+再",
    r"分别.+(?:相减|差值|差多少)",
)


def build_query_plan_v1(query: str, analysis_plan: dict[str, Any]) -> QueryPlanV1:
    """Build a deterministic skeleton before retrieval resolves active-build IDs."""

    metric_refs: list[SemanticRefV1] = []
    for hint in analysis_plan.get("metric_hints", []):
        metric_id = _METRIC_HINT_IDS.get(str(hint))
        if metric_id:
            metric_refs.append(SemanticRefV1(metric_id, str(hint)))
    metric_refs = _dedupe_refs(metric_refs)
    filters = _query_filters(analysis_plan.get("filter_requirements", []))
    time_range = analysis_plan.get("time_range", {})
    complexity = classify_query_complexity(query, analysis_plan)
    dimensions = tuple(
        SemanticRefV1(
            f"semantic_hint:dimension:{_DIMENSION_HINT_KEYS.get(str(label), _slug(label))}",
            str(label),
        )
        for label in analysis_plan.get("dimensions", [])
    )
    sort = _sort(metric_refs, dimensions, analysis_plan.get("sort_direction"))
    return QueryPlanV1(
        schema_version="query-plan/v1",
        intent=str(analysis_plan.get("intent") or "aggregate"),
        complexity=complexity,
        metrics=tuple(metric_refs),
        dimensions=dimensions,
        filters=tuple(filters),
        time=QueryTimeV1(
            field_id=None,
            start=time_range.get("start"),
            end=time_range.get("end"),
            label=time_range.get("label"),
            grain=analysis_plan.get("time_grain"),
        ),
        sort=sort,
        join_path=(),
        subplans=_subplans(complexity, metric_refs, dimensions, filters),
        limit=analysis_plan.get("top_n"),
        comparison=analysis_plan.get("comparison"),
        source_hints=_source_hints(analysis_plan),
    )


def resolve_query_plan_v1(
    plan: dict[str, Any],
    *,
    query: str,
    analysis_plan: dict[str, Any],
    metric_infos: Iterable[dict[str, Any]],
    table_infos: Iterable[dict[str, Any]],
    relationships: Iterable[dict[str, Any]],
) -> QueryPlanV1:
    """Bind the skeleton only to semantic objects present in the active build."""

    metric_candidates = list(metric_infos)
    relationship_candidates = list(relationships)
    expected_ids = {
        str(item.get("semantic_id"))
        for item in plan.get("metrics", [])
        if item.get("semantic_id")
    }
    metric_refs = _resolved_metrics(query, metric_candidates, expected_ids)
    available_columns = [
        column
        for table in table_infos
        for column in table.get("columns", [])
        if column.get("id")
    ]
    dimension_refs = _resolved_dimensions(
        analysis_plan.get("dimensions", []), available_columns
    )
    filters = _query_filters(analysis_plan.get("filter_requirements", []))
    time_range = analysis_plan.get("time_range", {})
    time_field = next(
        (
            str(metric.get("time_column"))
            for metric in metric_candidates
            if metric.get("id") in {item.semantic_id for item in metric_refs}
            and metric.get("time_column")
        ),
        None,
    )
    complexity = classify_query_complexity(
        query,
        analysis_plan,
        join_count=len(relationship_candidates),
    )
    relationship_ids = tuple(
        dict.fromkeys(
            str(relationship.get("id"))
            for relationship in relationship_candidates
            if relationship.get("id")
        )
    )
    sort = _sort(metric_refs, dimension_refs, analysis_plan.get("sort_direction"))
    return QueryPlanV1(
        schema_version="query-plan/v1",
        intent=str(analysis_plan.get("intent") or "aggregate"),
        complexity=complexity,
        metrics=tuple(metric_refs),
        dimensions=tuple(dimension_refs),
        filters=tuple(filters),
        time=QueryTimeV1(
            field_id=time_field,
            start=time_range.get("start"),
            end=time_range.get("end"),
            label=time_range.get("label"),
            grain=analysis_plan.get("time_grain"),
        ),
        sort=sort,
        join_path=relationship_ids,
        subplans=_subplans(complexity, metric_refs, dimension_refs, filters),
        limit=analysis_plan.get("top_n"),
        comparison=analysis_plan.get("comparison"),
        source_hints=_source_hints(analysis_plan),
    )


def classify_query_complexity(
    query: str,
    analysis_plan: dict[str, Any],
    *,
    join_count: int = 0,
) -> QueryComplexity:
    if any(re.search(pattern, query, flags=re.IGNORECASE) for pattern in _NESTED_PATTERNS):
        return QueryComplexity.NESTED
    filters = analysis_plan.get("filter_requirements", [])
    metric_hints = analysis_plan.get("metric_hints", [])
    comparison_filters = sum(
        str(item.get("location") or "where") == "any" for item in filters
    )
    if analysis_plan.get("comparison") == "difference" and comparison_filters >= 2:
        return QueryComplexity.NESTED
    if (
        analysis_plan.get("intent") in {"ranking", "trend", "compare", "detail"}
        or analysis_plan.get("comparison")
        or analysis_plan.get("dimensions")
        or len(filters) > 1
        or len(metric_hints) > 1
        or join_count > 0
    ):
        return QueryComplexity.NON_NESTED
    return QueryComplexity.EASY


def _resolved_metrics(
    query: str,
    metrics: list[dict[str, Any]],
    expected_ids: set[str],
) -> list[SemanticRefV1]:
    refs: list[SemanticRefV1] = []
    query_lower = query.lower()
    for metric in metrics:
        metric_id = str(metric.get("id") or "")
        aliases = [str(value) for value in metric.get("alias", [])]
        literal_match = any(alias and alias.lower() in query_lower for alias in aliases)
        literal_match = literal_match or str(metric.get("name") or "").lower() in query_lower
        if metric_id and (metric_id in expected_ids or literal_match):
            label = next((alias for alias in aliases if alias in query), str(metric.get("name")))
            refs.append(SemanticRefV1(metric_id, label))
    if not refs and expected_ids:
        for metric in metrics:
            metric_id = str(metric.get("id") or "")
            if metric_id in expected_ids:
                refs.append(SemanticRefV1(metric_id, str(metric.get("name") or metric_id)))
    return _dedupe_refs(refs)


def _resolved_dimensions(
    labels: Iterable[str], columns: list[dict[str, Any]]
) -> list[SemanticRefV1]:
    refs: list[SemanticRefV1] = []
    for raw_label in labels:
        label = str(raw_label)
        suffixes = _DIMENSION_SUFFIXES.get(label, ())
        matches = [
            column
            for column in columns
            if any(str(column.get("id", "")).endswith(f".{suffix}") for suffix in suffixes)
        ]
        if not matches:
            matches = [
                column
                for column in columns
                if any(
                    token and token in label
                    for token in [
                        str(column.get("name") or ""),
                        *[str(value) for value in column.get("alias", [])],
                    ]
                )
            ]
        for column in matches[:2]:
            refs.append(SemanticRefV1(str(column["id"]), label))
    return _dedupe_refs(refs)


def _query_filters(requirements: Iterable[dict[str, Any]]) -> list[QueryFilterV1]:
    filters: list[QueryFilterV1] = []
    for index, requirement in enumerate(requirements):
        operators = [str(value) for value in requirement.get("operators", [])]
        match_type = str(requirement.get("value_match") or "exact")
        filters.append(
            QueryFilterV1(
                filter_id=f"filter:{index}",
                field_ids=tuple(str(value) for value in requirement.get("columns", [])),
                operator=operators[0] if operators else match_type,
                values=tuple(str(value) for value in requirement.get("values", [])),
                label=str(requirement.get("label") or f"筛选 {index + 1}"),
                location=str(requirement.get("location") or "where"),
                filter_only=bool(requirement.get("filter_only")),
            )
        )
    return filters


def _sort(
    metrics: Iterable[SemanticRefV1],
    dimensions: Iterable[SemanticRefV1],
    direction: str | None,
) -> tuple[QuerySortV1, ...]:
    if direction not in {"asc", "desc"}:
        return ()
    target = next(iter(metrics), None) or next(iter(dimensions), None)
    return (QuerySortV1(target.semantic_id, direction),) if target else ()


def _subplans(
    complexity: QueryComplexity,
    metrics: Iterable[SemanticRefV1],
    dimensions: Iterable[SemanticRefV1],
    filters: Iterable[QueryFilterV1],
) -> tuple[QuerySubplanV1, ...]:
    if complexity is not QueryComplexity.NESTED:
        return ()
    metric_ids = tuple(item.semantic_id for item in metrics)
    dimension_ids = tuple(item.semantic_id for item in dimensions)
    filter_items = list(filters)
    comparison_filters = [item for item in filter_items if item.location == "any"]
    if len(comparison_filters) >= 2:
        return tuple(
            QuerySubplanV1(
                subplan_id=f"subplan:comparison:{index + 1}",
                purpose="comparison_operand",
                metric_ids=metric_ids,
                dimension_ids=dimension_ids,
                filter_ids=(item.filter_id,),
            )
            for index, item in enumerate(comparison_filters)
        )
    return (
        QuerySubplanV1(
            subplan_id="subplan:nested:1",
            purpose="nested_aggregation",
            metric_ids=metric_ids,
            dimension_ids=dimension_ids,
            filter_ids=tuple(item.filter_id for item in filter_items),
        ),
    )


def _source_hints(analysis_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "metric_hints": list(analysis_plan.get("metric_hints", [])),
        "dimensions": list(analysis_plan.get("dimensions", [])),
        "filters": list(analysis_plan.get("filters", [])),
        "filter_requirements": list(analysis_plan.get("filter_requirements", [])),
        "metric_requirements": list(analysis_plan.get("metric_requirements", [])),
        "time_range": dict(analysis_plan.get("time_range", {})),
        "time_grain": analysis_plan.get("time_grain"),
        "top_n": analysis_plan.get("top_n"),
        "sort_direction": analysis_plan.get("sort_direction"),
        "comparison": analysis_plan.get("comparison"),
    }


def _dedupe_refs(values: Iterable[SemanticRefV1]) -> list[SemanticRefV1]:
    return list({value.semantic_id: value for value in values}.values())


def _slug(value: object) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "-", str(value).lower()).strip("-")
    return slug or "semantic"
