"""Deterministic normalization for safe, semantically obvious DSL choices."""

from __future__ import annotations

from app.agent.dsl.schema import (
    Comparison,
    Dimension,
    DSLValidationError,
    FilterClause,
    FilterGroup,
    FilterOperator,
    Measure,
    OrderBy,
    QueryDSL,
)
from app.services.deterministic_sql_service import (
    find_catalog_metric,
    find_metric_by_alias,
)


def build_catalog_metric_dsl(
    query: str,
    metric_infos: list[dict],
    *,
    max_result_rows: int,
) -> QueryDSL | None:
    """Build the closed DSL equivalent of the existing canonical-metric SQL path."""

    metric = find_catalog_metric(query, metric_infos)
    if metric is None:
        return None
    recalled = {str(item["id"]): item for item in metric_infos}.get(str(metric["id"]))
    if recalled is None:
        return None
    alias = _matched_alias(query, recalled) or str(
        next(iter(recalled.get("alias", [])), recalled.get("name") or recalled["id"])
    )
    dimensions: list[Dimension] = []
    currency_column = recalled.get("currency_column")
    if currency_column:
        dimensions.append(Dimension(column=str(currency_column), alias="币种"))
    return QueryDSL(
        intent="aggregate",
        measures=[Measure(metric=str(recalled["id"]), alias=alias)],
        dimensions=dimensions,
        limit=max_result_rows,
    )


def build_status_compare_dsl(
    query: str,
    metric_infos: list[dict],
    table_infos: list[dict],
    *,
    max_result_rows: int,
) -> QueryDSL | None:
    """Build an exact two-status comparison without model interpretation.

    This shortcut is deliberately narrow: the question must name exactly two
    supported playback states, request a difference, name one recalled base metric,
    and have the status column in the current semantic context.
    """

    if "差" not in query:
        return None
    status_labels = {
        "完播": "completed",
        "中断": "interrupted",
        "失败": "failed",
    }
    matched_statuses = sorted(
        (
            (query.index(label), label, value)
            for label, value in status_labels.items()
            if label in query
        ),
        key=lambda item: item[0],
    )
    if len(matched_statuses) != 2:
        return None
    status_column = "play_session.play_status"
    if status_column not in {
        str(column.get("id")) for table in table_infos for column in table.get("columns", [])
    }:
        return None

    metric = find_metric_by_alias(query, metric_infos)
    if metric is None or "play_session." not in str(metric.get("formula", "")):
        return None
    alias = _matched_alias(query, metric) or str(
        next(iter(metric.get("alias", [])), metric.get("name") or metric["id"])
    )
    measures = [
        Measure(
            metric=str(metric["id"]),
            alias=f"{label}{alias}",
            filters=[
                FilterGroup(
                    clauses=[
                        FilterClause(
                            column=status_column,
                            operator=FilterOperator.EQ,
                            value=value,
                        )
                    ]
                )
            ],
        )
        for _, label, value in matched_statuses
    ]
    return QueryDSL(
        intent="compare",
        measures=measures,
        comparison=Comparison(
            operation="difference",
            left_measure=measures[0].alias,
            right_measure=measures[1].alias,
            alias=f"{matched_statuses[0][1]}与{matched_statuses[1][1]}{alias}差值",
        ),
        limit=max_result_rows,
    )


def normalize_query_dsl(
    query: str,
    dsl: QueryDSL,
    metric_infos: list[dict],
    table_infos: list[dict],
    analysis_plan: dict | None = None,
) -> QueryDSL:
    """Normalize choices that have one deterministic interpretation.

    The model still chooses the query shape, filters and display fields.  This pass
    only resolves an explicitly named metric, replaces an ID-only ranking label with
    a recalled human-readable field, removes a duplicate trend timestamp, and makes
    detail output include its recalled entity key.
    """

    measures = list(dsl.measures)
    matched_metric = find_metric_by_alias(query, metric_infos)
    if len(measures) == 1 and dsl.intent in {"aggregate", "trend", "ranking"}:
        if matched_metric is not None:
            measures[0] = measures[0].model_copy(update={"metric": str(matched_metric["id"])})
        elif dsl.intent == "aggregate" and not _metric_alias_occurs(
            query, measures[0].metric, metric_infos
        ):
            raise DSLValidationError("本次召回指标中没有与用户问题明确匹配的指标")

    dimensions = _normalize_ranking_dimensions(query, dsl, table_infos)
    dimensions = _align_ranking_dimensions_to_metric(dsl, measures, metric_infos, dimensions)
    dimensions = _normalize_trend_dimensions(dsl, measures, metric_infos, dimensions)
    order_by = list(dsl.order_by)
    filters = _ensure_required_filters(dsl.filters, analysis_plan or {})
    if dsl.intent == "detail":
        dimensions, primary_key = _ensure_detail_primary_key(dimensions, table_infos)
        dimensions = _normalize_detail_dimension_order(dimensions)
        if not order_by:
            recent_column = _recent_detail_column(query, dimensions)
            target = recent_column or primary_key
            if target:
                order_by = [OrderBy(target=target, direction="desc")]

    return dsl.model_copy(
        update={
            "measures": measures,
            "dimensions": dimensions,
            "filters": filters,
            "order_by": order_by,
        }
    )


def _matched_alias(query: str, metric: dict) -> str | None:
    normalized = query.lower()
    matches = [
        str(alias)
        for alias in metric.get("alias", [])
        if str(alias).strip() and str(alias).lower() in normalized
    ]
    return max(matches, key=len) if matches else None


def _metric_alias_occurs(query: str, metric_id: str, metric_infos: list[dict]) -> bool:
    metric = next(
        (item for item in metric_infos if str(item.get("id")) == metric_id),
        None,
    )
    return bool(metric and _matched_alias(query, metric))


def _normalize_ranking_dimensions(
    query: str,
    dsl: QueryDSL,
    table_infos: list[dict],
) -> list[Dimension]:
    if dsl.intent != "ranking":
        return list(dsl.dimensions)
    columns_by_table = _columns_by_table(table_infos)
    dimensions_by_table: dict[str, list[Dimension]] = {}
    table_order: list[str] = []
    for dimension in dsl.dimensions:
        table_name = dimension.column.rsplit(".", 1)[0]
        if table_name not in dimensions_by_table:
            table_order.append(table_name)
        dimensions_by_table.setdefault(table_name, []).append(dimension)

    normalized: list[Dimension] = []
    explicit_identifier = any(word in query.upper() for word in ("ID", "编号", "编码"))
    for table_name in table_order:
        table_dimensions = dimensions_by_table[table_name]
        candidates = [
            column
            for column in columns_by_table.get(table_name, [])
            if str(column.get("id", "")).endswith(("_name", "_title"))
            and not column.get("sensitive")
            and not column.get("filter_only")
        ]
        selected_ids = {item.column for item in table_dimensions}
        if (
            explicit_identifier
            or not candidates
            or (len(table_dimensions) == 1 and str(candidates[0].get("id")) in selected_ids)
        ):
            normalized.extend(table_dimensions)
            continue
        label = " ".join(str(item.alias or "") for item in table_dimensions)
        label = label.replace("名称", "").replace("ID", "")
        candidates.sort(
            key=lambda column: (
                -_display_score(column, label),
                str(column.get("id", "")),
            )
        )
        best = candidates[0]
        alias = next(
            (
                str(item.alias)
                for item in table_dimensions
                if item.alias and not str(item.alias).upper().endswith("ID")
            ),
            str(table_dimensions[0].alias or best.get("name") or "名称"),
        )
        normalized.append(Dimension(column=str(best["id"]), alias=alias))
    return normalized


def _display_score(column: dict, label: str) -> int:
    haystack = " ".join(
        [
            str(column.get("id", "")),
            str(column.get("name", "")),
            str(column.get("description", "")),
            *[str(alias) for alias in column.get("alias", [])],
        ]
    )
    return (10 if label and label in haystack else 0) + (
        2 if str(column.get("id", "")).endswith(("_name", "_title")) else 0
    )


def _align_ranking_dimensions_to_metric(
    dsl: QueryDSL,
    measures: list[Measure],
    metric_infos: list[dict],
    dimensions: list[Dimension],
) -> list[Dimension]:
    if dsl.intent != "ranking" or len(measures) != 1:
        return dimensions
    metric = next(
        (item for item in metric_infos if str(item.get("id")) == measures[0].metric),
        None,
    )
    if metric is None:
        return dimensions
    allowed = [str(item) for item in metric.get("dimensions", [])]
    normalized: list[Dimension] = []
    for dimension in dimensions:
        column_name = dimension.column.rsplit(".", 1)[-1]
        same_name = [column for column in allowed if column.rsplit(".", 1)[-1] == column_name]
        if dimension.column not in allowed and len(same_name) == 1:
            normalized.append(Dimension(column=same_name[0], alias=dimension.alias))
        else:
            normalized.append(dimension)
    return normalized


def _normalize_trend_dimensions(
    dsl: QueryDSL,
    measures: list[Measure],
    metric_infos: list[dict],
    dimensions: list[Dimension],
) -> list[Dimension]:
    if dsl.intent != "trend":
        return dimensions
    time_column = dsl.time_column
    if not time_column and len(measures) == 1:
        metric = next(
            (item for item in metric_infos if str(item.get("id")) == measures[0].metric),
            None,
        )
        if metric and metric.get("time_column"):
            time_column = str(metric["time_column"])
    return [item for item in dimensions if item.column != time_column]


def _ensure_detail_primary_key(
    dimensions: list[Dimension],
    table_infos: list[dict],
) -> tuple[list[Dimension], str | None]:
    if not dimensions:
        return dimensions, None
    table_name = dimensions[0].column.rsplit(".", 1)[0]
    primary_key = f"{table_name}.id"
    available = {
        str(column.get("id")) for column in _columns_by_table(table_infos).get(table_name, [])
    }
    if primary_key not in available:
        return dimensions, None
    if any(item.column == primary_key for item in dimensions):
        return dimensions, primary_key
    return [Dimension(column=primary_key, alias="ID"), *dimensions], primary_key


def _recent_detail_column(query: str, dimensions: list[Dimension]) -> str | None:
    if "最近" not in query:
        return None
    return next(
        (
            item.column
            for item in dimensions
            if item.column.rsplit(".", 1)[-1].endswith(("_at", "_date", "_time"))
        ),
        None,
    )


def _normalize_detail_dimension_order(dimensions: list[Dimension]) -> list[Dimension]:
    """Keep detail projections stable when a status and timestamp are both shown."""

    status_positions = [
        index
        for index, item in enumerate(dimensions)
        if item.column.rsplit(".", 1)[-1].endswith("_status")
    ]
    time_positions = [
        index
        for index, item in enumerate(dimensions)
        if item.column.rsplit(".", 1)[-1].endswith(("_at", "_date", "_time"))
    ]
    if not status_positions or not time_positions or min(time_positions) < min(status_positions):
        return dimensions
    status_columns = [dimensions[index] for index in status_positions]
    without_status = [
        item for index, item in enumerate(dimensions) if index not in status_positions
    ]
    insert_at = (
        max(
            index
            for index, item in enumerate(without_status)
            if item.column.rsplit(".", 1)[-1].endswith(("_at", "_date", "_time"))
        )
        + 1
    )
    return [*without_status[:insert_at], *status_columns, *without_status[insert_at:]]


def _ensure_required_filters(
    filters: list[FilterGroup],
    analysis_plan: dict,
) -> list[FilterGroup]:
    normalized = list(filters)
    existing_columns = {clause.column for group in normalized for clause in group.clauses}
    for requirement in analysis_plan.get("filter_requirements", []):
        columns = requirement.get("columns", [])
        values = requirement.get("values", [])
        if (
            requirement.get("location") != "where"
            or requirement.get("value_match") != "exact"
            or len(columns) != 1
            or len(values) != 1
            or str(columns[0]) in existing_columns
        ):
            continue
        column = str(columns[0])
        normalized.append(
            FilterGroup(
                clauses=[
                    FilterClause(
                        column=column,
                        operator=FilterOperator.EQ,
                        value=values[0],
                    )
                ]
            )
        )
        existing_columns.add(column)
    return normalized


def _columns_by_table(table_infos: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for table in table_infos:
        table_name = str(table.get("name") or table.get("id") or "")
        result[table_name] = list(table.get("columns", []))
    return result
