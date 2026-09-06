"""Deterministic and result-bound chart specifications."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChartType = Literal["table", "kpi", "bar", "line", "pie"]

_NUMERIC_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
_TIME_NAME_PATTERN = re.compile(
    r"(日期|时间|月份|时刻|date|time|day|month|dt)", re.IGNORECASE
)
_DATE_VALUE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$"),
    re.compile(r"^\d{4}/\d{2}(?:/\d{2})?$"),
)
_COMPACT_DATE_PATTERN = re.compile(r"^\d{8}$")
_MAX_BAR_ROWS = 30
_MAX_PIE_ROWS = 12


class ChartSpecValidationError(ValueError):
    """Raised when a proposed chart references incompatible result data."""


class ChartSpecV1(BaseModel):
    """A small, versioned visualization contract bound to result columns."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["chart-spec/v1"] = "chart-spec/v1"
    type: ChartType
    title: str = Field(min_length=1, max_length=200)
    dimension: str | None = None
    metrics: list[str] = Field(default_factory=list, max_length=8)
    series: str | None = None
    source: Literal["deterministic", "llm_validated"] = "deterministic"

    @model_validator(mode="after")
    def validate_shape(self) -> ChartSpecV1:
        if len(self.metrics) != len(set(self.metrics)):
            raise ValueError("图表指标列不能重复")
        if self.type == "table":
            if self.dimension is not None or self.metrics or self.series is not None:
                raise ValueError("table 规格不能声明维度、指标或 series")
        elif self.type == "kpi":
            if self.dimension is not None or len(self.metrics) != 1 or self.series:
                raise ValueError("kpi 必须且只能声明一个指标列")
        elif self.type in {"bar", "pie"}:
            if self.dimension is None or len(self.metrics) != 1:
                raise ValueError(f"{self.type} 必须声明一个维度列和一个指标列")
            if self.type == "pie" and self.series is not None:
                raise ValueError("pie 不支持额外 series 列")
        elif self.type == "line" and (self.dimension is None or not self.metrics):
            raise ValueError("line 必须声明时间维度列和至少一个指标列")
        if self.series is not None and len(self.metrics) != 1:
            raise ValueError("声明 series 时只能使用一个指标列")
        return self


def build_chart_spec(
    columns: list[str],
    rows: list[dict[str, Any]],
    suggestion: dict[str, Any] | None = None,
) -> ChartSpecV1:
    """Use a valid suggestion when possible, otherwise return a safe default."""

    if suggestion is not None:
        try:
            proposed = ChartSpecV1.model_validate(suggestion).model_copy(
                update={"source": "llm_validated"}
            )
            return validate_chart_spec(proposed, columns, rows)
        except (ValueError, ChartSpecValidationError):
            # Suggestions are advisory. Invalid fields or types never reach clients.
            pass
    return _deterministic_chart_spec(columns, rows)


def validate_chart_spec(
    spec: ChartSpecV1 | dict[str, Any],
    columns: list[str],
    rows: list[dict[str, Any]],
) -> ChartSpecV1:
    """Fail closed unless every referenced field belongs to the real result."""

    try:
        validated = (
            spec if isinstance(spec, ChartSpecV1) else ChartSpecV1.model_validate(spec)
        )
    except ValueError as exc:
        raise ChartSpecValidationError(str(exc)) from exc

    available = set(columns)
    referenced = {
        value
        for value in [validated.dimension, validated.series, *validated.metrics]
        if value is not None
    }
    unknown = sorted(referenced - available)
    if unknown:
        raise ChartSpecValidationError(f"图表引用了不存在的结果列：{', '.join(unknown)}")
    if validated.dimension in validated.metrics or validated.series in validated.metrics:
        raise ChartSpecValidationError("维度、series 与指标列不能重叠")

    if validated.type == "table":
        return validated
    if not rows:
        raise ChartSpecValidationError("空结果只能使用 table")
    if not all(_is_numeric_column(metric, rows) for metric in validated.metrics):
        raise ChartSpecValidationError("图表指标必须是数值列")

    if validated.type == "kpi":
        if len(rows) != 1:
            raise ChartSpecValidationError("kpi 仅适用于单行结果")
        return validated

    if len(rows) < 2:
        raise ChartSpecValidationError("图表至少需要两行结果")
    if validated.type == "line" and not _is_time_column(validated.dimension or "", rows):
        raise ChartSpecValidationError("line 的维度必须是时间列")
    if validated.type == "bar" and len(rows) > _MAX_BAR_ROWS:
        raise ChartSpecValidationError(f"bar 最多支持 {_MAX_BAR_ROWS} 个类目")
    if validated.type == "pie" and len(rows) > _MAX_PIE_ROWS:
        raise ChartSpecValidationError(f"pie 最多支持 {_MAX_PIE_ROWS} 个类目")
    if validated.series and _is_numeric_column(validated.series, rows):
        raise ChartSpecValidationError("series 必须是非数值分类列")
    return validated


def _deterministic_chart_spec(
    columns: list[str], rows: list[dict[str, Any]]
) -> ChartSpecV1:
    table = ChartSpecV1(type="table", title="数据表格")
    if not columns or not rows:
        return table

    numeric_columns = [column for column in columns if _is_numeric_column(column, rows)]
    if len(rows) == 1 and len(columns) == 1 and numeric_columns:
        return validate_chart_spec(
            ChartSpecV1(type="kpi", title=columns[0], metrics=[columns[0]]),
            columns,
            rows,
        )

    time_column = next(
        (column for column in columns if _is_time_column(column, rows)), None
    )
    if time_column and numeric_columns and len(rows) >= 2:
        metrics = [column for column in numeric_columns if column != time_column]
        if metrics:
            return validate_chart_spec(
                ChartSpecV1(
                    type="line",
                    title=f"{time_column}趋势",
                    dimension=time_column,
                    metrics=metrics[:8],
                ),
                columns,
                rows,
            )

    dimension = next(
        (column for column in columns if column not in numeric_columns), None
    )
    if dimension and numeric_columns and 2 <= len(rows) <= _MAX_BAR_ROWS:
        return validate_chart_spec(
            ChartSpecV1(
                type="bar",
                title=f"{numeric_columns[0]}按{dimension}对比",
                dimension=dimension,
                metrics=[numeric_columns[0]],
            ),
            columns,
            rows,
        )
    return table


def _is_numeric_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    return isinstance(value, str) and bool(_NUMERIC_PATTERN.fullmatch(value.strip()))


def _is_numeric_column(column: str, rows: list[dict[str, Any]]) -> bool:
    values = [row.get(column) for row in rows if row.get(column) is not None]
    return bool(values) and all(_is_numeric_value(value) for value in values)


def _is_time_column(column: str, rows: list[dict[str, Any]]) -> bool:
    values = [row.get(column) for row in rows if row.get(column) is not None]
    if not values:
        return False
    name_hint = bool(_TIME_NAME_PATTERN.search(column))
    for value in values:
        if isinstance(value, (date, datetime)):
            continue
        text = str(value).strip()
        if any(pattern.fullmatch(text) for pattern in _DATE_VALUE_PATTERNS):
            continue
        if name_hint and _COMPACT_DATE_PATTERN.fullmatch(text):
            continue
        return False
    return True
