"""Compile a validated QueryDSL to one safe, inspectable SELECT statement."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.agent.dsl.schema import (
    Comparison,
    FilterClause,
    FilterGroup,
    FilterOperator,
    Measure,
    QueryDSL,
    TimeRange,
)


class DSLCompilationError(ValueError):
    """Raised when a semantically valid DSL cannot be deterministically compiled."""


@dataclass(frozen=True)
class _Metric:
    id: str
    formula: str
    filters: tuple[str, ...]
    time_column: str | None


class DSLCompiler:
    """Render semantic metric definitions, dimensions and allowed joins into SQL."""

    def __init__(self, max_result_rows: int):
        self.max_result_rows = max_result_rows

    def compile(
        self,
        dsl: QueryDSL,
        metric_infos: list[dict],
        relationships: list[dict],
        table_infos: list[dict] | None = None,
        *,
        dialect: str,
    ) -> str:
        metrics = {
            str(item["id"]): _Metric(
                id=str(item["id"]),
                formula=str(item["formula"]),
                filters=tuple(str(value) for value in item.get("filters", [])),
                time_column=str(item["time_column"]) if item.get("time_column") else None,
            )
            for item in metric_infos
        }
        sqlglot_dialect = _sqlglot_dialect(dialect)
        selected_metrics = [(measure, metrics[measure.metric]) for measure in dsl.measures]
        global_conditions = self._compile_filter_groups(dsl.filters)
        time_column = self._resolve_time_column(dsl, selected_metrics)
        if dsl.time_range:
            if not time_column:
                raise DSLCompilationError("时间范围缺少可用时间字段")
            global_conditions.append(
                self._compile_time_range(time_column, dsl.time_range, sqlglot_dialect)
            )

        required_tables = self._required_tables(dsl, selected_metrics, time_column)
        if not required_tables:
            raise DSLCompilationError("DSL 未引用任何可查询的数据表")
        root, joins = self._compile_joins(required_tables, relationships, selected_metrics)
        select_parts, group_parts, alias_by_target = self._compile_select(
            dsl,
            selected_metrics,
            time_column,
            global_conditions,
            sqlglot_dialect,
            _available_columns(table_infos or []),
        )
        sql = f"SELECT {', '.join(select_parts)} FROM {root}"
        if joins:
            sql += " " + " ".join(joins)
        if global_conditions:
            sql += " WHERE " + " AND ".join(f"({condition})" for condition in global_conditions)
        if group_parts:
            sql += " GROUP BY " + ", ".join(group_parts)
        order_parts = self._compile_order_by(dsl, alias_by_target)
        if order_parts:
            sql += " ORDER BY " + ", ".join(order_parts)
        sql += f" LIMIT {min(dsl.limit, self.max_result_rows)}"
        try:
            expression = sqlglot.parse_one(sql, read=sqlglot_dialect)
        except sqlglot.errors.ParseError as exc:
            raise DSLCompilationError("DSL 编译出的 SQL 无法解析") from exc
        if not isinstance(expression, exp.Select):
            raise DSLCompilationError("DSL 编译结果必须为 SELECT")
        return expression.sql(dialect=sqlglot_dialect, pretty=False)

    def _compile_select(
        self,
        dsl: QueryDSL,
        selected_metrics: list[tuple[Measure, _Metric]],
        time_column: str | None,
        global_conditions: list[str],
        dialect: str,
        available_columns: set[str],
    ) -> tuple[list[str], list[str], dict[str, str]]:
        select_parts: list[str] = []
        group_parts: list[str] = []
        aliases: dict[str, str] = {}
        if dsl.intent == "trend":
            if not time_column or not dsl.time_grain:
                raise DSLCompilationError("趋势查询缺少时间字段或粒度")
            expression = self._time_bucket(time_column, dsl.time_grain, dialect)
            alias = "时间"
            select_parts.append(f"{expression} AS `{alias}`")
            group_parts.append(expression)
            aliases[time_column] = alias
        metric_tables = {
            table for _, metric in selected_metrics for table in _table_refs(metric.formula)
        }
        for dimension in dsl.dimensions:
            if dsl.intent == "trend" and dimension.column == time_column:
                continue
            alias = dimension.alias or dimension.column.rsplit(".", 1)[-1]
            select_parts.append(f"{dimension.column} AS `{alias}`")
            dimension_table = dimension.column.rsplit(".", 1)[0]
            identity_column = f"{dimension_table}.id"
            if (
                dsl.intent == "ranking"
                and dimension_table not in metric_tables
                and identity_column in available_columns
                and identity_column != dimension.column
                and identity_column not in group_parts
            ):
                group_parts.append(identity_column)
            group_parts.append(dimension.column)
            aliases[dimension.column] = alias
        if dsl.intent == "detail":
            return select_parts, [], aliases

        multi_measure = len(selected_metrics) > 1 or dsl.comparison is not None
        expressions: dict[str, str] = {}
        for measure, metric in selected_metrics:
            local_conditions = [*metric.filters, *self._compile_filter_groups(measure.filters)]
            if measure.time_range:
                local_time_column = metric.time_column or time_column
                if not local_time_column:
                    raise DSLCompilationError(f"指标 {metric.id} 不支持独立时间范围")
                local_conditions.append(
                    self._compile_time_range(local_time_column, measure.time_range, dialect)
                )
            formula = metric.formula
            if local_conditions:
                if multi_measure:
                    formula = self._conditional_formula(formula, local_conditions)
                else:
                    global_conditions.extend(local_conditions)
            expressions[measure.alias] = formula
            select_parts.append(f"{formula} AS `{measure.alias}`")
            aliases[measure.alias] = measure.alias

        if dsl.comparison:
            select_parts.append(self._compile_comparison(dsl.comparison, expressions))
            aliases[dsl.comparison.alias] = dsl.comparison.alias
        return select_parts, group_parts, aliases

    def _required_tables(
        self,
        dsl: QueryDSL,
        selected_metrics: list[tuple[Measure, _Metric]],
        time_column: str | None,
    ) -> set[str]:
        tables = {column.rsplit(".", 1)[0] for column in self._dsl_columns(dsl)}
        if time_column:
            tables.add(time_column.rsplit(".", 1)[0])
        for _, metric in selected_metrics:
            tables |= _table_refs(metric.formula)
            for filter_sql in metric.filters:
                tables |= _table_refs(filter_sql)
        return tables

    def _compile_joins(
        self,
        required_tables: set[str],
        relationships: list[dict],
        selected_metrics: list[tuple[Measure, _Metric]],
    ) -> tuple[str, list[str]]:
        root = self._preferred_root(required_tables, selected_metrics)
        connected = {root}
        remaining = set(required_tables) - connected
        joins: list[str] = []
        ordered_relationships = sorted(relationships, key=lambda item: str(item.get("id", "")))
        while remaining:
            candidates = [
                relationship
                for relationship in ordered_relationships
                if (
                    relationship.get("source_table") in connected
                    and relationship.get("target_table") in remaining
                )
                or (
                    relationship.get("target_table") in connected
                    and relationship.get("source_table") in remaining
                )
            ]
            if not candidates:
                raise DSLCompilationError(
                    f"已召回关系无法连接数据表：{', '.join(sorted(remaining))}"
                )
            relationship = candidates[0]
            source = str(relationship["source_table"])
            target = str(relationship["target_table"])
            join_table = target if source in connected else source
            on = (
                f"{source}.{relationship['source_column']} = "
                f"{target}.{relationship['target_column']}"
            )
            condition = relationship.get("condition")
            if condition:
                on += f" AND ({condition})"
            joins.append(f"JOIN {join_table} ON {on}")
            connected.add(join_table)
            remaining.remove(join_table)
        return root, joins

    def _preferred_root(
        self, required_tables: set[str], selected_metrics: list[tuple[Measure, _Metric]]
    ) -> str:
        for _, metric in selected_metrics:
            references = _table_refs(metric.formula)
            if references:
                return sorted(references)[0]
        return sorted(required_tables)[0]

    def _compile_filter_groups(self, groups: Iterable[FilterGroup]) -> list[str]:
        compiled: list[str] = []
        for group in groups:
            joiner = " AND " if group.logic == "and" else " OR "
            compiled.append(
                joiner.join(self._compile_filter_clause(clause) for clause in group.clauses)
            )
        return compiled

    def _compile_filter_clause(self, clause: FilterClause) -> str:
        operator = clause.operator
        if operator is FilterOperator.IS_NULL:
            return f"{clause.column} IS NULL"
        if operator is FilterOperator.IS_NOT_NULL:
            return f"{clause.column} IS NOT NULL"
        if operator is FilterOperator.CONTAINS:
            return f"{clause.column} LIKE {_literal('%' + str(clause.value) + '%')}"
        if operator in {FilterOperator.IN, FilterOperator.NOT_IN}:
            values = ", ".join(_literal(value) for value in clause.value)  # type: ignore[arg-type]
            keyword = "IN" if operator is FilterOperator.IN else "NOT IN"
            return f"{clause.column} {keyword} ({values})"
        if operator is FilterOperator.BETWEEN:
            values = clause.value  # type: ignore[assignment]
            return f"{clause.column} BETWEEN {_literal(values[0])} AND {_literal(values[1])}"
        symbols = {
            FilterOperator.EQ: "=",
            FilterOperator.NE: "<>",
            FilterOperator.GT: ">",
            FilterOperator.GTE: ">=",
            FilterOperator.LT: "<",
            FilterOperator.LTE: "<=",
        }
        return f"{clause.column} {symbols[operator]} {_literal(clause.value)}"

    def _compile_time_range(self, column: str, time_range: TimeRange, dialect: str) -> str:
        conditions: list[str] = []
        if time_range.start:
            conditions.append(f"{column} >= {_literal(time_range.start)}")
        if time_range.end:
            end = _literal(time_range.end)
            if dialect == "postgres":
                conditions.append(f"{column} < ({end}::date + INTERVAL '1 day')")
            elif dialect == "clickhouse":
                conditions.append(f"{column} < addDays({end}, 1)")
            else:
                conditions.append(f"{column} < DATE_ADD({end}, INTERVAL 1 DAY)")
        if not conditions:
            raise DSLCompilationError("时间范围不能为空")
        return " AND ".join(conditions)

    def _resolve_time_column(
        self, dsl: QueryDSL, selected_metrics: list[tuple[Measure, _Metric]]
    ) -> str | None:
        if dsl.time_column:
            return dsl.time_column
        time_columns = {metric.time_column for _, metric in selected_metrics if metric.time_column}
        if len(time_columns) == 1:
            return next(iter(time_columns))
        if dsl.time_range or dsl.time_grain:
            raise DSLCompilationError("多个指标使用不同时间字段，DSL 必须明确 time_column")
        return None

    def _time_bucket(self, column: str, grain: str, dialect: str) -> str:
        if dialect == "postgres":
            expressions = {
                item: f"DATE_TRUNC('{item}', {column})" for item in ("hour", "day", "week", "month")
            }
        elif dialect == "clickhouse":
            expressions = {
                "hour": f"toStartOfHour({column})",
                "day": f"toStartOfDay({column})",
                "week": f"toStartOfWeek({column})",
                "month": f"toStartOfMonth({column})",
            }
        else:
            expressions = {
                "hour": f"DATE_FORMAT({column}, '%Y-%m-%d %H:00:00')",
                "day": f"DATE({column})",
                "week": f"DATE_FORMAT({column}, '%x-%v')",
                "month": f"DATE_FORMAT({column}, '%Y-%m')",
            }
        return expressions[grain]

    def _conditional_formula(self, formula: str, conditions: list[str]) -> str:
        match = re.fullmatch(r"(?is)(COUNT|SUM|AVG|MIN|MAX)\((DISTINCT\s+)?(.+)\)", formula.strip())
        if not match:
            raise DSLCompilationError("该指标公式不支持分组对比；请回退 legacy 链路或补充专用指标")
        function, distinct, argument = match.groups()
        predicate = " AND ".join(f"({condition})" for condition in conditions)
        distinct_prefix = distinct or ""
        return f"{function.upper()}({distinct_prefix}CASE WHEN {predicate} THEN {argument} END)"

    def _compile_comparison(self, comparison: Comparison, expressions: dict[str, str]) -> str:
        left = expressions[comparison.left_measure]
        right = expressions[comparison.right_measure]
        if comparison.operation == "difference":
            expression = f"({left}) - ({right})"
        elif comparison.operation == "ratio":
            expression = f"({left}) / NULLIF(({right}), 0)"
        else:
            expression = f"(({left}) - ({right})) / NULLIF(({right}), 0)"
        return f"{expression} AS `{comparison.alias}`"

    def _compile_order_by(self, dsl: QueryDSL, aliases: dict[str, str]) -> list[str]:
        if dsl.intent == "trend":
            return ["`时间` ASC"]
        compiled = [
            f"`{aliases.get(order.target, order.target)}` {order.direction.upper()}"
            for order in dsl.order_by
        ]
        targets = {order.target for order in dsl.order_by}
        if dsl.intent == "ranking":
            for dimension in dsl.dimensions:
                if dimension.column in targets or (dimension.alias and dimension.alias in targets):
                    continue
                alias = aliases.get(
                    dimension.column,
                    dimension.alias or dimension.column.rsplit(".", 1)[-1],
                )
                compiled.append(f"`{alias}` ASC")
        if dsl.intent == "detail":
            primary_key = next(
                (item.column for item in dsl.dimensions if item.column.endswith(".id")),
                None,
            )
            if primary_key and primary_key not in targets:
                compiled.append(f"`{aliases.get(primary_key, primary_key)}` DESC")
        return compiled

    def _dsl_columns(self, dsl: QueryDSL) -> set[str]:
        columns = {dimension.column for dimension in dsl.dimensions}
        columns.update(clause.column for group in dsl.filters for clause in group.clauses)
        for measure in dsl.measures:
            columns.update(clause.column for group in measure.filters for clause in group.clauses)
        if dsl.time_column:
            columns.add(dsl.time_column)
        return columns


def _table_refs(sql: str) -> set[str]:
    try:
        parsed = sqlglot.parse_one(f"SELECT {sql}", read="mysql")
    except sqlglot.errors.ParseError:
        return set()
    return {column.table for column in parsed.find_all(exp.Column) if column.table}


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _sqlglot_dialect(dialect: str) -> str:
    return {"postgresql": "postgres", "doris": "mysql"}.get(dialect.lower(), dialect.lower())


def _available_columns(table_infos: list[dict]) -> set[str]:
    return {
        str(column.get("id") or f"{table.get('name')}.{column.get('name')}")
        for table in table_infos
        for column in table.get("columns", [])
        if column.get("id") or (table.get("name") and column.get("name"))
    }
