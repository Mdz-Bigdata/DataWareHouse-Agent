"""Deterministic platform SQL Plan generation for high-risk ChatBI queries."""

from __future__ import annotations

import re
from typing import Any

from app.services.ai.runners.chatbi.run_state import DataRunState


def _clean_identifier(value: str) -> str:
    return value.strip().strip("`\"[]")


def _extract_clause(sql: str, start: str, stops: str) -> str:
    match = re.search(
        rf"\b{start}\b\s+(.+?)(?=\b(?:{stops})\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return " ".join(match.group(1).split()) if match else ""


def build_platform_sql_plan(
    runner: Any,
    state: DataRunState,
    *,
    sql: str,
    data_source: str,
    dataset_name: str,
) -> dict[str, Any]:
    """Build an auditable plan from the SQL and already-authorized Schema context."""
    table_matches = re.findall(
        r"\b(?:from|join)\s+([`\"\[\]\w.]+)", sql, flags=re.IGNORECASE
    )
    tables = list(dict.fromkeys(_clean_identifier(value) for value in table_matches))
    if not tables and dataset_name:
        tables = [dataset_name]

    known_fields: list[str] = []
    for columns in state.schema_table_columns.values():
        for column in columns:
            name = str(column or "").strip()
            if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", sql, re.IGNORECASE):
                if name not in known_fields:
                    known_fields.append(name)

    select_clause = _extract_clause(sql, "select", "from")
    metrics = [
        " ".join(value.split())
        for value in re.findall(
            r"(?:count|sum|avg|min|max|median|percentile\w*)\s*\([^)]*\)"
            r"(?:\s*/\s*(?:count|sum|avg|min|max)\s*\([^)]*\))?"
            r"(?:\s+as\s+[\w`\"\[\]]+)?",
            select_clause,
            flags=re.IGNORECASE,
        )
    ]
    where_clause = _extract_clause(sql, "where", r"group\s+by|having|order\s+by|limit")
    group_clause = _extract_clause(sql, r"group\s+by", r"having|order\s+by|limit")
    join_conditions = [
        " ".join(value.split())
        for value in re.findall(
            r"\bjoin\s+.+?\s+on\s+(.+?)(?=\bjoin\b|\bwhere\b|\bgroup\s+by\b|$)",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]

    time_range = "无"
    if where_clause and re.search(r"date|time|year|month|day|日期|时间|created_at|updated_at", where_clause, re.IGNORECASE):
        time_range = where_clause

    risk_notes = ["平台依据已授权 Schema 与待执行 SQL 自动生成；后续仍需通过字段、权限和性能门禁"]
    if metrics:
        risk_notes.append("包含聚合或比率计算，需确认分母为零与聚合粒度")
    if join_conditions:
        risk_notes.append("包含 JOIN，需确认关联键与多对多放大风险")

    return {
        "goal": str(getattr(runner, "_standalone_query", "") or "").strip() or "执行当前数据查询",
        "tables": tables,
        "fields": known_fields,
        "metrics": metrics,
        "filters": [where_clause] if where_clause else [],
        "time_range": time_range,
        "grain": f"按 {group_clause} 聚合" if group_clause else "明细粒度",
        "joins": join_conditions or ["无"],
        "risk_notes": risk_notes,
        "data_source": data_source,
        "dataset_name": dataset_name,
    }
