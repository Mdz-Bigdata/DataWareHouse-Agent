"""Build executable reference SQL from the audio metric catalog."""

from __future__ import annotations

from pathlib import Path

import sqlglot
from omegaconf import OmegaConf

from app.metadata.schema_catalog import parse_mysql_ddl

PROJECT_ROOT = Path(__file__).parents[2]
DDL_PATH = PROJECT_ROOT / "tools" / "audio_data" / "sql" / "audio.sql"
METRICS_PATH = PROJECT_ROOT / "conf" / "domains" / "audio" / "metrics.yaml"

_catalog = parse_mysql_ddl(DDL_PATH)


def load_audio_metrics() -> list[dict]:
    """Return the configured audio metrics as plain dictionaries."""

    return [
        {**dict(metric), "id": str(metric.get("id") or metric["name"])}
        for metric in OmegaConf.load(METRICS_PATH).get("metrics", [])
    ]


def build_metric_reference_sql(metric: dict) -> str:
    """Create an executable truth-query for one semantic metric.

    Currency metrics must be grouped by their configured currency column so that
    the benchmark never compares a semantically ambiguous cross-currency sum.
    """

    primary_table = _primary_table(metric)
    formula = str(metric["formula"])
    filters = [str(value) for value in metric.get("filters", [])]
    currency_column = metric.get("currency_column")
    references = _extract_table_refs(formula) | _extract_table_refs(" AND ".join(filters))
    if currency_column:
        references |= _extract_table_refs(str(currency_column))
    joins = _join_clause(primary_table, references)
    if currency_column:
        select_clause = f"{currency_column} AS currency, {formula} AS value"
        group_clause = f" GROUP BY {currency_column} ORDER BY {currency_column}"
    else:
        select_clause = f"{formula} AS value"
        group_clause = ""
    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ""
    return f"SELECT {select_clause} FROM {primary_table} {joins}{where_clause}{group_clause} LIMIT 500"


def metric_question(metric: dict) -> str:
    """Produce a concise natural-language question for a metric definition."""

    aliases = [str(alias) for alias in metric.get("alias", [])]
    label = aliases[0] if aliases else str(metric["name"])
    currency_column = metric.get("currency_column")
    if currency_column:
        return f"请按币种统计平台{label}"
    if metric.get("snapshot"):
        return f"平台当前{label}是多少"
    return f"平台累计{label}是多少"


def _primary_table(metric: dict) -> str:
    sources = [str(metric.get("formula", "")), *metric.get("relevant_columns", [])]
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
    try:
        parsed = sqlglot.parse_one(f"SELECT {sql}", read="mysql")
    except Exception:
        return set()
    return {column.table for column in parsed.find_all(sqlglot.exp.Column) if column.table}


def _join_clause(primary_table: str, extra_tables: set[str]) -> str:
    joins: list[str] = []
    joined = {primary_table}
    remaining = extra_tables - joined
    while remaining:
        matched = False
        for relationship in _catalog.relationships:
            if relationship.source_table in joined and relationship.target_table in remaining:
                joins.append(
                    f"JOIN {relationship.target_table} ON {relationship.source_table}."
                    f"{relationship.source_column} = {relationship.target_table}."
                    f"{relationship.target_column}"
                )
                joined.add(relationship.target_table)
                remaining.remove(relationship.target_table)
                matched = True
                break
            if relationship.target_table in joined and relationship.source_table in remaining:
                joins.append(
                    f"JOIN {relationship.source_table} ON {relationship.source_table}."
                    f"{relationship.source_column} = {relationship.target_table}."
                    f"{relationship.target_column}"
                )
                joined.add(relationship.source_table)
                remaining.remove(relationship.source_table)
                matched = True
                break
        if not matched:
            raise ValueError(
                f"cannot derive join path from {primary_table} to {sorted(remaining)}"
            )
    return " ".join(joins)
