"""Shared generation helpers."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..db import db


def count_table(table_name: str) -> int:
    row = db.fetch_one(f"SELECT COUNT(*) AS c FROM `{table_name}`")
    return int(row["c"]) if row else 0


def fetch_all(table_name: str, columns: str = "*") -> list[dict[str, Any]]:
    return db.fetch_all(f"SELECT {columns} FROM `{table_name}`")


def fetch_id_map(table_name: str, code_column: str) -> dict[str, int]:
    rows = db.fetch_all(f"SELECT id, `{code_column}` AS code FROM `{table_name}`")
    return {str(row["code"]): int(row["id"]) for row in rows}


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, int):
        return value
    text = str(value).replace(",", "").strip()
    if not text:
        return default
    if text.endswith("亿"):
        return int(float(text[:-1]) * 100000000)
    if text.endswith("万"):
        return int(float(text[:-1]) * 10000)
    try:
        return int(float(text))
    except ValueError:
        return default


def decimal_amount(value: Any, default: str = "0.00") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value)).quantize(Decimal("0.01"))


def parse_datetime(value: Any, fallback: datetime) -> datetime:
    if value is None or value == "":
        return fallback
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%m-%d":
                parsed = parsed.replace(year=fallback.year)
            return parsed
        except ValueError:
            continue
    return fallback
