"""Baseline SQL validation for all metrics in the audio domain.

Parses `conf/domains/audio/metrics.yaml` and verifies that every metric's
`formula` and `filters` can be turned into syntactically valid SQL whose
column references exist in the physical schema.

When `RUN_AUDIO_DATA_ACCEPTANCE=1` is set, the generated baseline SELECT is
also executed against the configured warehouse to catch runtime errors.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlglot
from omegaconf import OmegaConf
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.conf.app_config import app_config
from app.metadata.schema_catalog import parse_mysql_ddl

PROJECT_ROOT = Path(__file__).parents[1]
DDL_PATH = PROJECT_ROOT / "tools" / "audio_data" / "sql" / "audio.sql"
METRICS_PATH = PROJECT_ROOT / "conf" / "domains" / "audio" / "metrics.yaml"


_physical_catalog = parse_mysql_ddl(DDL_PATH)


def _primary_table(metric: dict) -> str:
    """Infer the primary table from the first column reference in formula/relevant_columns."""
    sources = [metric.get("formula", ""), *metric.get("relevant_columns", [])]
    for source in sources:
        try:
            parsed = sqlglot.parse_one(f"SELECT {source}", read="mysql")
        except Exception:
            continue
        for column in parsed.find_all(sqlglot.exp.Column):
            if column.table:
                return column.table
    raise ValueError(f"cannot infer primary table for metric {metric['name']}")


def _extract_table_refs(sql: str) -> set[str]:
    refs: set[str] = set()
    try:
        parsed = sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return refs
    for column in parsed.find_all(sqlglot.exp.Column):
        if column.table:
            refs.add(column.table)
    return refs


def _join_clause(primary_table: str, extra_tables: set[str]) -> str:
    joins: list[str] = []
    joined = {primary_table}
    remaining = extra_tables - joined

    for rel in _physical_catalog.relationships:
        if not remaining:
            break
        if rel.source_table in joined and rel.target_table in remaining:
            joins.append(
                f"JOIN {rel.target_table} ON {rel.source_table}.{rel.source_column} = "
                f"{rel.target_table}.{rel.target_column}"
            )
            joined.add(rel.target_table)
            remaining.discard(rel.target_table)
        elif rel.target_table in joined and rel.source_table in remaining:
            joins.append(
                f"JOIN {rel.source_table} ON {rel.source_table}.{rel.source_column} = "
                f"{rel.target_table}.{rel.target_column}"
            )
            joined.add(rel.source_table)
            remaining.discard(rel.source_table)

    return " ".join(joins)


def _build_baseline_sql(metric: dict) -> str:
    table = _primary_table(metric)
    formula = metric["formula"]
    filters = metric.get("filters", [])

    sql_without_from = f"SELECT {formula} AS value FROM {table}"
    if filters:
        sql_without_from += " WHERE " + " AND ".join(filters)

    all_refs = _extract_table_refs(formula) | _extract_table_refs(" AND ".join(filters))
    joins = _join_clause(table, all_refs)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    return f"SELECT {formula} AS value FROM {table} {joins} {where_clause} LIMIT 1".strip()


def _extract_column_refs(sql: str) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    try:
        parsed = sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return refs
    for column in parsed.find_all(sqlglot.exp.Column):
        table = column.table or ""
        name = column.name
        if table:
            refs.add((table, name))
    return refs


_metrics = OmegaConf.load(METRICS_PATH).get("metrics", [])


@pytest.mark.parametrize("metric", _metrics, ids=lambda m: m["name"])
def test_metric_baseline_sql_syntax(metric: dict):
    sql = _build_baseline_sql(metric)
    try:
        sqlglot.parse_one(sql, read="mysql")
    except Exception as exc:
        pytest.fail(f"metric '{metric['name']}' baseline SQL failed to parse: {sql}\n{exc}")


@pytest.mark.parametrize("metric", _metrics, ids=lambda m: m["name"])
def test_metric_baseline_columns_exist(metric: dict):
    sql = _build_baseline_sql(metric)
    refs = _extract_column_refs(sql)
    missing = [
        f"{table}.{column}"
        for table, column in refs
        if table not in _physical_catalog.tables
        or column not in _physical_catalog.tables[table].columns
    ]
    if missing:
        pytest.fail(
            f"metric '{metric['name']}' references missing columns: {missing}\n"
            f"baseline SQL: {sql}"
        )


@pytest.mark.integration
@pytest.mark.parametrize("metric", _metrics, ids=lambda m: m["name"])
def test_metric_baseline_sql_executes(metric: dict):
    if os.getenv("RUN_AUDIO_DATA_ACCEPTANCE") != "1":
        pytest.skip("set RUN_AUDIO_DATA_ACCEPTANCE=1 to run warehouse execution")

    sql = _build_baseline_sql(metric)

    async def _run():
        engine = create_async_engine(
            f"mysql+asyncmy://{app_config.db_dw.user}:{app_config.db_dw.password}"
            f"@{app_config.db_dw.host}:{app_config.db_dw.port}"
            f"/{app_config.db_dw.database}?charset=utf8mb4",
            future=True,
        )
        async with engine.connect() as conn:
            await conn.execute(text(sql))
        await engine.dispose()

    import asyncio

    asyncio.run(_run())
