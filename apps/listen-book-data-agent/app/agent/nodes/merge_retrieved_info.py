from __future__ import annotations

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.query_plan import resolve_query_plan_v1
from app.agent.schema_selection import (
    filter_island_tables,
    relationship_condition_column,
    score_by_literal_match,
    shortest_relationship_paths,
    without_sensitive_columns,
)
from app.agent.state import (
    ColumnInfoState,
    DataAgentState,
    MetricInfoState,
    RelationshipState,
    TableInfoState,
)
from app.core.log import logger
from app.entities.column_info import ColumnInfo
from app.entities.relationship_info import RelationshipInfo
from app.services.deterministic_sql_service import find_catalog_metric, find_metric_by_alias


async def merge_retrieved_info(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Resolve recalled metadata into a safe schema plus minimal join paths."""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": "补齐数据关系", "status": "running"})
    try:
        repository = runtime.context["meta_mysql_repository"]
        build_id = await repository.get_active_build_id()
        if not build_id:
            raise LookupError("没有可用的元数据知识库，请先执行知识库构建")

        metrics = _select_metrics(
            state.get("retrieved_metrics", []), state.get("analysis_plan", {})
        )
        analysis_plan = state.get("analysis_plan", {})
        filter_only_column_ids = _filter_only_column_ids(analysis_plan)
        # Phase 2.3：字面提权重排。query 原文命中的指标/字段提到前面，提升 LLM 注意力。
        query = state.get("query", "")
        catalog_metric = (
            find_catalog_metric(query, []) or find_metric_by_alias(query, [], include_catalog=True)
            if query
            else None
        )
        if catalog_metric:
            try:
                canonical_metric = await repository.get_metric_info_by_id(
                    str(catalog_metric["id"]), build_id
                )
                metrics = [canonical_metric, *metrics]
            except LookupError:
                logger.warning("规范指标 {} 未在当前知识库版本中找到", catalog_metric["id"])
        metrics = list({metric.id: metric for metric in metrics}.values())
        if query:
            metrics = [
                item
                for item, _ in score_by_literal_match(
                    metrics,
                    query,
                    name_of=lambda m: m.name,
                    alias_of=lambda m: m.alias,
                    description_of=lambda m: m.description,
                )
            ]
        columns: dict[str, ColumnInfo] = {
            item.id: item
            for item in without_sensitive_columns(
                # Phase 2.3：召回字段按字面命中度提权后再去敏感、入 dict
                [
                    col
                    for col, _ in (
                        score_by_literal_match(
                            state.get("retrieved_columns", []),
                            query,
                            name_of=lambda c: c.name,
                            alias_of=lambda c: c.alias,
                            description_of=lambda c: c.description,
                        )
                        if query
                        else [(c, 0) for c in state.get("retrieved_columns", [])]
                    )
                ]
            )
        }

        # 规则化计划中的必需列优先于向量阈值，防止“男性/黄金会员”等低相似度词漏列。
        for column_id in _required_column_ids(analysis_plan):
            column = await repository.get_column_info_by_id(column_id, build_id)
            if not column.sensitive or column_id in filter_only_column_ids:
                columns.setdefault(column_id, column)

        for metric in metrics:
            for column_id in metric.relevant_columns:
                columns.setdefault(
                    column_id,
                    await repository.get_column_info_by_id(column_id, build_id),
                )

        # 用户明确命中的指标同时携带其授权维度，使排行/趋势归一化可以
        # 将同名字段对齐到指标所属事实表，而不会引入未召回字段。
        if catalog_metric:
            matched = next(
                (item for item in metrics if item.id == str(catalog_metric["id"])),
                None,
            )
            if matched:
                for column_id in matched.dimensions:
                    columns.setdefault(
                        column_id,
                        await repository.get_column_info_by_id(column_id, build_id),
                    )

        for value in state.get("retrieved_values", []):
            column = columns.get(value.column_id)
            if column is None:
                column = await repository.get_column_info_by_id(value.column_id, build_id)
                columns[value.column_id] = column
            if not column.sensitive and value.value not in column.examples:
                column.examples.append(value.value)

        columns = _retain_prompt_columns(columns.values(), filter_only_column_ids)
        seed_table_ids = _prioritized_table_ids(columns.values(), metrics, analysis_plan, query)
        all_relationships = await repository.get_all_relationships(build_id)
        relationship_paths = shortest_relationship_paths(seed_table_ids, all_relationships)

        for relationship in relationship_paths:
            for column_id in _relationship_column_ids(relationship):
                if column_id not in columns:
                    columns[column_id] = await repository.get_column_info_by_id(column_id, build_id)

        columns = _retain_prompt_columns(columns.values(), filter_only_column_ids)
        table_ids = list(
            dict.fromkeys(
                [item.table_id for item in columns.values()]
                + [item.source_table for item in relationship_paths]
                + [item.target_table for item in relationship_paths]
            )
        )
        # Phase 2.3：孤岛过滤。丢弃与任何 relationship 无连通的表，防 LLM 幻觉 JOIN。
        # 注意：单表查询（relationship_paths 为空）时 filter_island_tables 原样返回，不误杀。
        table_ids = filter_island_tables(table_ids, relationship_paths)
        for table_id in table_ids:
            for key_column in await repository.get_key_columns_by_table_id(table_id, build_id):
                if not key_column.sensitive:
                    columns.setdefault(key_column.id, key_column)

        # 排行和明细需要可读标签；仅从当前激活语义层补全 name/title 字段，
        # 不扩大到未授权表或敏感列。
        allowed_columns = await repository.list_allowed_column_infos(build_id)
        for display_column in allowed_columns:
            if display_column.table_id in table_ids and display_column.name.endswith(
                ("_name", "_title")
            ):
                columns.setdefault(display_column.id, display_column)

        table_infos = await _to_table_states(
            repository,
            table_ids,
            columns,
            build_id,
            filter_only_column_ids,
        )
        metric_infos = [_to_metric_state(metric) for metric in metrics]
        relationships = [_to_relationship_state(item) for item in relationship_paths]
        table_infos, metric_infos, relationships = _apply_catalog_acl(
            table_infos,
            metric_infos,
            relationships,
            state.get("access_policy", {}),
        )
        query_plan = resolve_query_plan_v1(
            state.get("query_plan", {}),
            query=query,
            analysis_plan=analysis_plan,
            metric_infos=metric_infos,
            table_infos=table_infos,
            relationships=relationships,
        ).to_state()
        warnings = state.get("retrieval_warnings", [])
        semantic_term_matches = _public_semantic_term_matches(
            state.get("semantic_terms", []),
            metric_infos=metric_infos,
            table_infos=table_infos,
            access_policy=state.get("access_policy", {}),
        )

        writer(
            {
                "type": "context",
                "analysis_plan": state.get("analysis_plan", {}),
                "query_plan": query_plan,
                "build_id": build_id,
                "tables": [item["name"] for item in table_infos],
                "relationships": relationships,
                "semantic_term_matches": semantic_term_matches,
                "warnings": warnings,
            }
        )
        writer({"type": "progress", "step": "补齐数据关系", "status": "success"})
        logger.info(
            "结构化上下文完成：{} 张表、{} 个指标、{} 条关系",
            len(table_infos),
            len(metric_infos),
            len(relationships),
        )
        return {
            "build_id": build_id,
            "table_infos": table_infos,
            "metric_infos": metric_infos,
            "relationships": relationships,
            "query_plan": query_plan,
            "semantic_term_matches": semantic_term_matches,
        }
    except Exception:
        writer({"type": "progress", "step": "补齐数据关系", "status": "error"})
        logger.exception("补齐数据关系失败")
        raise


def _select_metrics(metrics, analysis_plan: dict):
    hints = " ".join(
        [*analysis_plan.get("metric_hints", []), *analysis_plan.get("dimensions", [])]
    ).lower()
    if hints:
        metrics = sorted(
            metrics,
            key=lambda item: (
                -sum(
                    term in " ".join([item.name, item.description, *item.alias]).lower()
                    for term in analysis_plan.get("metric_hints", [])
                ),
                item.name,
            ),
        )
    return list({item.id: item for item in metrics}.values())[:5]


def _public_semantic_term_matches(
    terms: list[dict],
    *,
    metric_infos: list[MetricInfoState],
    table_infos: list[TableInfoState],
    access_policy: dict,
) -> list[dict]:
    """Expose only published term bindings grounded in authorized recalled semantics."""

    authorized_ids = _authorized_semantic_ids(
        metric_infos=metric_infos,
        table_infos=table_infos,
        access_policy=access_policy,
    )
    matches: list[dict] = []
    for term in terms:
        bindings = [
            {
                "kind": str(binding.get("kind") or ""),
                "semantic_id": str(binding.get("semantic_id") or ""),
            }
            for binding in term.get("bindings", [])
            if str(binding.get("semantic_id") or "") in authorized_ids
        ]
        if not bindings:
            continue
        matches.append(
            {
                "term_key": str(term.get("term_key") or ""),
                "standard_term": str(term.get("standard_term") or ""),
                "version": int(term.get("version") or 1),
                "bindings": bindings,
            }
        )
    return matches[:5]


def _authorized_semantic_ids(
    *,
    metric_infos: list[MetricInfoState],
    table_infos: list[TableInfoState],
    access_policy: dict,
) -> set[str]:
    table_acl = {
        str(table): {str(column) for column in columns}
        for table, columns in (access_policy.get("table_acl") or {}).items()
    }
    if access_policy.get("admin_bypass") or "*" in table_acl:
        return {
            *[str(metric["id"]) for metric in metric_infos],
            *[str(table["id"]) for table in table_infos],
            *[
                str(column["id"])
                for table in table_infos
                for column in table.get("columns", [])
            ],
        }
    authorized: set[str] = set()
    for table in table_infos:
        table_id = str(table["id"])
        allowed_columns = table_acl.get(table_id, set())
        if not allowed_columns:
            continue
        authorized.add(table_id)
        for column in table.get("columns", []):
            column_id = str(column["id"])
            column_name = column_id.rsplit(".", 1)[-1]
            if "*" in allowed_columns or column_name in allowed_columns:
                authorized.add(column_id)
    for metric in metric_infos:
        relevant = [str(value) for value in metric.get("relevant_columns", [])]
        if relevant and all(value in authorized for value in relevant):
            authorized.add(str(metric["id"]))
    return authorized


def _apply_catalog_acl(
    table_infos: list[TableInfoState],
    metric_infos: list[MetricInfoState],
    relationships: list[RelationshipState],
    access_policy: dict,
) -> tuple[list[TableInfoState], list[MetricInfoState], list[RelationshipState]]:
    """Keep unauthorized schema out of QueryPlan, prompts and public context."""

    if not access_policy or access_policy.get("admin_bypass"):
        return table_infos, metric_infos, relationships
    table_acl = {
        str(table): {str(column) for column in columns}
        for table, columns in (access_policy.get("table_acl") or {}).items()
    }
    if "*" in table_acl:
        return table_infos, metric_infos, relationships

    allowed_tables: list[TableInfoState] = []
    allowed_field_ids: set[str] = set()
    for table in table_infos:
        table_id = str(table["id"])
        allowed_columns = table_acl.get(table_id)
        if not allowed_columns:
            continue
        columns = [
            column
            for column in table.get("columns", [])
            if "*" in allowed_columns
            or str(column["id"]).rsplit(".", 1)[-1] in allowed_columns
        ]
        if not columns:
            continue
        allowed_field_ids.update(str(column["id"]) for column in columns)
        allowed_tables.append({**table, "columns": columns})

    allowed_table_ids = {str(table["id"]) for table in allowed_tables}
    allowed_metrics = [
        metric
        for metric in metric_infos
        if metric.get("relevant_columns")
        and all(
            str(column_id) in allowed_field_ids
            for column_id in metric.get("relevant_columns", [])
        )
    ]
    allowed_relationships = [
        relationship
        for relationship in relationships
        if str(relationship["source_table"]) in allowed_table_ids
        and str(relationship["target_table"]) in allowed_table_ids
        and f"{relationship['source_table']}.{relationship['source_column']}"
        in allowed_field_ids
        and f"{relationship['target_table']}.{relationship['target_column']}"
        in allowed_field_ids
    ]
    return allowed_tables, allowed_metrics, allowed_relationships


def _prioritized_table_ids(
    columns,
    metrics,
    analysis_plan: dict | None = None,
    query: str = "",
) -> list[str]:
    scores: dict[str, int] = {}
    for requirement in (analysis_plan or {}).get("metric_requirements", []):
        column_id = requirement.get("column")
        if column_id:
            table_id = column_id.rsplit(".", 1)[0]
            scores[table_id] = scores.get(table_id, 0) + 8
    for metric in metrics:
        for column_id in metric.relevant_columns:
            table_id = column_id.rsplit(".", 1)[0]
            scores[table_id] = scores.get(table_id, 0) + 4
    for column in columns:
        scores[column.table_id] = scores.get(column.table_id, 0) + 1
        if query and any(
            token and token.lower() in query.lower() for token in [column.name, *column.alias]
        ):
            scores[column.table_id] = scores.get(column.table_id, 0) + 8
    return [
        table_id for table_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]


def _relationship_column_ids(relationship: RelationshipInfo) -> list[str]:
    column_ids = [
        f"{relationship.source_table}.{relationship.source_column}",
        f"{relationship.target_table}.{relationship.target_column}",
    ]
    discriminator = relationship_condition_column(relationship)
    if discriminator:
        column_ids.append(f"{relationship.source_table}.{discriminator}")
    return list(dict.fromkeys(column_ids))


async def _to_table_states(
    repository,
    table_ids,
    columns,
    build_id,
    filter_only_column_ids: set[str],
) -> list[TableInfoState]:
    by_table: dict[str, list[ColumnInfo]] = {}
    for column in columns.values():
        by_table.setdefault(column.table_id, []).append(column)
    table_states: list[TableInfoState] = []
    for table_id in table_ids:
        table = await repository.get_table_info_by_id(table_id, build_id)
        table_states.append(
            TableInfoState(
                id=table.id,
                name=table.name,
                role=table.role,
                description=table.description,
                alias=table.alias,
                columns=[
                    _to_column_state(item, filter_only_column_ids)
                    for item in by_table.get(table_id, [])
                ],
            )
        )
    return table_states


def _to_column_state(
    column: ColumnInfo, filter_only_column_ids: set[str] | None = None
) -> ColumnInfoState:
    return ColumnInfoState(
        id=column.id,
        name=column.name,
        type=column.type,
        role=column.role,
        examples=column.examples,
        description=column.description,
        alias=column.alias,
        table_id=column.table_id,
        sensitive=column.sensitive,
        filter_only=column.id in (filter_only_column_ids or set()),
    )


def _required_column_ids(analysis_plan: dict) -> list[str]:
    column_ids: list[str] = []
    for requirement in analysis_plan.get("filter_requirements", []):
        column_ids.extend(requirement.get("columns", []))
    for requirement in analysis_plan.get("metric_requirements", []):
        column_id = requirement.get("column")
        if column_id:
            column_ids.append(column_id)
    return list(dict.fromkeys(column_ids))


def _filter_only_column_ids(analysis_plan: dict) -> set[str]:
    return {
        column_id
        for requirement in analysis_plan.get("filter_requirements", [])
        if requirement.get("filter_only")
        for column_id in requirement.get("columns", [])
    }


def _retain_prompt_columns(columns, filter_only_column_ids: set[str]) -> dict[str, ColumnInfo]:
    """保留普通字段及本次明确请求的聚合过滤专用敏感字段。"""

    return {
        item.id: item for item in columns if not item.sensitive or item.id in filter_only_column_ids
    }


def _to_metric_state(metric) -> MetricInfoState:
    return MetricInfoState(
        id=metric.id,
        name=metric.name,
        description=metric.description,
        relevant_columns=metric.relevant_columns,
        alias=metric.alias,
        formula=metric.formula,
        filters=metric.filters,
        time_column=metric.time_column,
        unit=metric.unit,
        currency_column=metric.currency_column,
        dimensions=metric.dimensions,
        snapshot=metric.snapshot,
    )


def _to_relationship_state(relationship: RelationshipInfo) -> RelationshipState:
    return RelationshipState(
        id=relationship.id,
        source_table=relationship.source_table,
        source_column=relationship.source_column,
        target_table=relationship.target_table,
        target_column=relationship.target_column,
        relationship_type=relationship.relationship_type,
        condition=relationship.condition,
        physical=relationship.physical,
    )
