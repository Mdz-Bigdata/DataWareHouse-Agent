"""Shared insert helpers for generators."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .config import GENERATION_DEFAULTS
from .db import db
from .progress import (
    advance_table_progress,
    finish_table_progress,
    start_table_progress,
)

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def build_insert_sql(table_name: str, columns: list[str]) -> str:
    column_sql = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO `{table_name}` ({column_sql}) VALUES ({placeholders})"


def chunked_rows(
    rows: list[tuple[Any, ...]], batch_size: int
) -> Iterable[list[tuple[Any, ...]]]:
    for start in range(0, len(rows), batch_size):
        yield rows[start : start + batch_size]


def _local_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None, microsecond=0)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt)
                if fmt == "%Y-%m-%d":
                    return datetime.combine(parsed.date(), datetime.min.time())
                return parsed
            except ValueError:
                continue
    return None


def _clamp_created_at(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    if "created_at" not in row:
        return row
    created_at = _coerce_datetime(row["created_at"])
    if created_at is None or created_at <= now:
        return row
    normalized = dict(row)
    normalized["created_at"] = now
    return normalized


def insert_dict_rows(table_name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    now = _local_now()
    normalized_rows = [_clamp_created_at(row, now) for row in rows]
    columns = list(normalized_rows[0])
    sql = build_insert_sql(table_name, columns)
    params = [tuple(row[column] for column in columns) for row in normalized_rows]
    batch_size = int(GENERATION_DEFAULTS.get("batch_size", 5000))

    start_table_progress(table_name, len(params))
    count = 0
    try:
        for batch in chunked_rows(params, batch_size):
            inserted = db.executemany(sql, batch)
            count += inserted
            advance_table_progress(table_name, inserted)
    finally:
        finish_table_progress(table_name, count)
    return count


def insert_dict_rows_stream(
    table_name: str,
    rows: Iterable[dict[str, Any]],
    *,
    total_rows: int | None = None,
    build_step_name: str | None = None,
    batch_size: int | None = None,
) -> int:
    size = batch_size or int(GENERATION_DEFAULTS.get("batch_size", 5000))
    now = _local_now()
    columns: list[str] | None = None
    sql = ""
    params: list[tuple[Any, ...]] = []
    count = 0
    start_table_progress(table_name, total_rows or 0)
    try:
        for row in rows:
            normalized = _clamp_created_at(row, now)
            if columns is None:
                columns = list(normalized)
                sql = build_insert_sql(table_name, columns)
            params.append(tuple(normalized[column] for column in columns))
            if len(params) >= size:
                inserted = db.executemany(sql, params)
                count += inserted
                advance_table_progress(table_name, inserted)
                params.clear()
        if params:
            inserted = db.executemany(sql, params)
            count += inserted
            advance_table_progress(table_name, inserted)
    finally:
        finish_table_progress(table_name, count)
    return count
