"""The safe, serialisable intermediate representation for analytics queries."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class DSLValidationError(ValueError):
    """Raised when a DSL document is structurally or semantically invalid."""


class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    BETWEEN = "between"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


Scalar = str | int | float | bool | None


class DSLModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(DSLModel):
    start: str | None = None
    end: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> TimeRange:
        for value in (self.start, self.end):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("时间范围必须使用 YYYY-MM-DD") from exc
        if self.start and self.end and self.start > self.end:
            raise ValueError("时间范围开始日期不能晚于结束日期")
        return self


class FilterClause(DSLModel):
    column: str = Field(min_length=3, max_length=120)
    operator: FilterOperator
    value: Scalar | list[Scalar] = None

    @model_validator(mode="after")
    def validate_value(self) -> FilterClause:
        if self.operator in {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}:
            if self.value is not None:
                raise ValueError("空值操作符不能携带 value")
        elif self.operator in {FilterOperator.IN, FilterOperator.NOT_IN, FilterOperator.BETWEEN}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.operator.value} 操作符必须携带非空数组")
            if self.operator is FilterOperator.BETWEEN and len(self.value) != 2:
                raise ValueError("between 操作符必须携带两个值")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.operator.value} 操作符不能携带数组")
        return self


class FilterGroup(DSLModel):
    logic: Literal["and", "or"] = "and"
    clauses: list[FilterClause] = Field(min_length=1, max_length=8)


class Measure(DSLModel):
    metric: str = Field(min_length=1, max_length=120)
    alias: str = Field(min_length=1, max_length=60)
    filters: list[FilterGroup] = Field(default_factory=list, max_length=8)
    time_range: TimeRange | None = None


class Dimension(DSLModel):
    column: str = Field(min_length=3, max_length=120)
    alias: str | None = Field(default=None, max_length=60)


class OrderBy(DSLModel):
    target: str = Field(min_length=1, max_length=120)
    direction: Literal["asc", "desc"] = "asc"


class Comparison(DSLModel):
    operation: Literal["difference", "ratio", "percent_change"]
    left_measure: str = Field(min_length=1, max_length=60)
    right_measure: str = Field(min_length=1, max_length=60)
    alias: str = Field(min_length=1, max_length=60)


class QueryDSL(DSLModel):
    """Versioned execution plan with identifiers, never arbitrary SQL fragments."""

    version: Literal["1"] = "1"
    intent: Literal["aggregate", "trend", "ranking", "compare", "detail"]
    measures: list[Measure] = Field(default_factory=list, max_length=5)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=8)
    filters: list[FilterGroup] = Field(default_factory=list, max_length=8)
    time_range: TimeRange | None = None
    time_column: str | None = Field(default=None, max_length=120)
    time_grain: Literal["hour", "day", "week", "month"] | None = None
    comparison: Comparison | None = None
    order_by: list[OrderBy] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=500, ge=1, le=500)

    @model_validator(mode="after")
    def validate_shape(self) -> QueryDSL:
        aliases = [measure.alias for measure in self.measures]
        if len(aliases) != len(set(aliases)):
            raise ValueError("指标别名不能重复")
        if self.intent == "detail":
            if self.measures:
                raise ValueError("明细查询不能包含聚合指标")
            if not self.dimensions:
                raise ValueError("明细查询至少需要一个输出维度")
        elif not self.measures:
            raise ValueError("聚合、趋势、排行和对比查询至少需要一个指标")
        if self.intent == "trend" and not self.time_grain:
            raise ValueError("趋势查询必须指定时间粒度")
        if self.intent == "ranking" and (not self.dimensions or not self.order_by):
            raise ValueError("排行查询必须指定维度和排序")
        if self.intent == "compare":
            if self.comparison is None:
                raise ValueError("对比查询必须指定 comparison")
            if {self.comparison.left_measure, self.comparison.right_measure} - set(aliases):
                raise ValueError("对比对象必须引用已有指标别名")
        elif self.comparison is not None:
            raise ValueError("只有对比查询可以指定 comparison")
        if self.time_grain and not (self.time_range or self.time_column):
            raise ValueError("时间粒度需要时间范围或时间字段")
        return self


def parse_query_dsl(value: str | dict[str, Any]) -> QueryDSL:
    """Parse a model response, tolerating a Markdown JSON fence but nothing else."""

    try:
        if isinstance(value, dict):
            return QueryDSL.model_validate(value)
        text = value.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        start = text.find("{")
        if start < 0:
            raise DSLValidationError("LLM 未返回 JSON 对象")
        payload, _ = json.JSONDecoder().raw_decode(text[start:])
        return QueryDSL.model_validate(payload)
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, DSLValidationError):
            raise
        raise DSLValidationError(f"QueryDSL 格式无效：{exc}") from exc


def validate_query_dsl(
    dsl: QueryDSL,
    metric_infos: list[dict],
    table_infos: list[dict],
    analysis_plan: dict | None,
    max_result_rows: int,
) -> QueryDSL:
    """Confirm all DSL references are present in this request's recalled semantic context."""

    metrics = {str(metric.get("id")): metric for metric in metric_infos}
    columns = _available_columns(table_infos)
    unknown_metrics = [measure.metric for measure in dsl.measures if measure.metric not in metrics]
    if unknown_metrics:
        raise DSLValidationError(f"指标未在本次语义上下文中召回：{', '.join(unknown_metrics)}")
    if dsl.intent == "aggregate" and dsl.dimensions:
        allowed_dimensions: set[str] = set()
        for measure in dsl.measures:
            metric = metrics[measure.metric]
            allowed_dimensions.update(str(item) for item in metric.get("dimensions", []))
            if metric.get("currency_column"):
                allowed_dimensions.add(str(metric["currency_column"]))
        unsupported_dimensions = [
            dimension.column
            for dimension in dsl.dimensions
            if dimension.column not in allowed_dimensions
        ]
        if unsupported_dimensions:
            raise DSLValidationError(
                "聚合查询包含指标未授权的分组维度：" + ", ".join(unsupported_dimensions)
            )
    if dsl.intent == "detail" and not any(
        dimension.column.endswith(".id") for dimension in dsl.dimensions
    ):
        raise DSLValidationError("明细查询必须包含已召回的实体主键字段")
    _validate_columns([dimension.column for dimension in dsl.dimensions], columns)
    _validate_columns(_filter_columns(dsl.filters), columns)
    for measure in dsl.measures:
        _validate_columns(_filter_columns(measure.filters), columns)
    if dsl.time_column:
        _validate_columns([dsl.time_column], columns)
    if dsl.limit > max_result_rows:
        raise DSLValidationError(f"limit 不能超过系统上限 {max_result_rows}")

    plan = analysis_plan or {}
    planned_intent = plan.get("intent")
    if planned_intent and planned_intent != dsl.intent:
        raise DSLValidationError(f"DSL 意图 {dsl.intent} 与分析计划 {planned_intent} 不一致")
    planned_range = plan.get("time_range") or {}
    if planned_range.get("start") or planned_range.get("end"):
        if dsl.time_range is None:
            raise DSLValidationError("分析计划要求时间范围，但 DSL 未保留")
        if (
            planned_range.get("start") != dsl.time_range.start
            or planned_range.get("end") != dsl.time_range.end
        ):
            raise DSLValidationError("DSL 时间范围与分析计划不一致")
    if plan.get("top_n") is not None and dsl.limit != int(plan["top_n"]):
        raise DSLValidationError("DSL limit 必须等于分析计划中的 Top N")
    return dsl


def _available_columns(table_infos: list[dict]) -> set[str]:
    columns: set[str] = set()
    for table in table_infos:
        table_name = str(table.get("name") or table.get("id") or "")
        for column in table.get("columns", []):
            column_id = str(column.get("id") or "")
            column_name = str(column.get("name") or "")
            if column_id:
                columns.add(column_id)
            if table_name and column_name:
                columns.add(f"{table_name}.{column_name}")
    return columns


def _filter_columns(groups: list[FilterGroup]) -> list[str]:
    return [clause.column for group in groups for clause in group.clauses]


def _validate_columns(requested: list[str], available: set[str]) -> None:
    unknown = [column for column in requested if column not in available]
    if unknown:
        raise DSLValidationError(f"字段未在本次语义上下文中召回：{', '.join(unknown)}")
