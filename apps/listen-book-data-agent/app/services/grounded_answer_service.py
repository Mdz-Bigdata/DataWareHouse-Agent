"""Create concise explanations strictly from executed query evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class GroundedAnswer:
    summary: str
    row_count: int
    columns: list[str]
    metrics: list[str]
    time_range: str
    sql: str

    def to_event(self) -> dict[str, Any]:
        return asdict(self)


def build_grounded_answer(
    *,
    sql: str,
    rows: list[dict[str, Any]],
    metric_infos: list[dict],
    analysis_plan: dict,
) -> GroundedAnswer:
    """Describe only returned values; no causal or trend claims are inferred."""

    columns = list(rows[0].keys()) if rows else []
    metrics = [str(item["name"]) for item in metric_infos if item.get("name")]
    time_range = _format_time_range(analysis_plan.get("time_range", {}))
    metric_text = "、".join(metrics) if metrics else "授权字段查询"

    if not rows:
        summary = f"已执行查询（{metric_text}，时间范围：{time_range}），未返回数据。"
    elif len(rows) == 1:
        summary = (
            f"已执行查询（{metric_text}，时间范围：{time_range}），返回 1 行："
            f"{_format_row(rows[0])}。"
        )
    else:
        examples = "；".join(_format_row(row) for row in rows[:3])
        suffix = "" if len(rows) <= 3 else "（仅展示前 3 行摘要）"
        summary = (
            f"已执行查询（{metric_text}，时间范围：{time_range}），共返回 {len(rows)} 行。"
            f"前几行结果：{examples}。{suffix}"
        )

    return GroundedAnswer(
        summary=summary,
        row_count=len(rows),
        columns=columns,
        metrics=metrics,
        time_range=time_range,
        sql=sql,
    )


def _format_time_range(time_range: dict) -> str:
    label = time_range.get("label")
    start = time_range.get("start")
    end = time_range.get("end")
    if label and start and end:
        return f"{label}（{start} 至 {end}）"
    if start and end:
        return f"{start} 至 {end}"
    return "未限定"


def _format_row(row: dict[str, Any]) -> str:
    values = [f"{key}={_format_value(value)}" for key, value in list(row.items())[:4]]
    return "，".join(values)


def _format_value(value: Any) -> str:
    if value is None:
        return "空"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)
