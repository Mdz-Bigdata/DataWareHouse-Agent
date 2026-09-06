"""Build business-analysis navigation strictly from authorized Schema output."""

from __future__ import annotations

import re
from typing import Any

import yaml

_HEADER_RE = re.compile(
    r"---\s*\[Schema:\d+\]\s+type=(?P<type>\w+)"
    r"(?:\s+dataset=(?P<dataset>[^\s]+))?"
    r"(?:\s+table=(?P<table>[^\s]+))?[^\n]*---\s*\n",
    re.I,
)
_NUMERIC_TYPES = ("int", "decimal", "numeric", "float", "double", "real", "number")
_TIME_TYPES = ("date", "time", "timestamp", "datetime")


def _blocks(schema_output: str) -> list[tuple[dict[str, str], dict[str, Any]]]:
    text = str(schema_output or "")
    matches = list(_HEADER_RE.finditer(text))
    blocks: list[tuple[dict[str, str], dict[str, Any]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        try:
            body = yaml.safe_load(text[match.end():end].strip()) or {}
        except yaml.YAMLError:
            body = {}
        if isinstance(body, dict):
            blocks.append(({key: value or "" for key, value in match.groupdict().items()}, body))
    return blocks


def _field(column: dict[str, Any], *, table: str, dataset: str) -> dict[str, str]:
    physical = str(column.get("physical_name") or column.get("name") or "").strip()
    term = str(column.get("term") or column.get("description") or physical).strip()
    return {
        "physical_name": physical,
        "label": term or physical,
        "type": str(column.get("type") or "").strip(),
        "table": table,
        "dataset": dataset,
    }


def build_metadata_guide(schema_output: str) -> dict[str, Any]:
    """Return navigation whose every identifier is evidenced by the Schema input."""
    datasets: list[str] = []
    tables: list[dict[str, str]] = []
    metrics: list[dict[str, str]] = []
    dimensions: list[dict[str, str]] = []
    time_fields: list[dict[str, str]] = []
    for header, body in _blocks(schema_output):
        dataset = str(header.get("dataset") or body.get("dataset") or "").strip()
        table = str(
            header.get("table") or body.get("table_name") or body.get("physical_name") or ""
        ).strip()
        if dataset and dataset not in datasets:
            datasets.append(dataset)
        if table:
            tables.append({
                "physical_name": table,
                "label": str(body.get("table_term") or body.get("term") or table).strip(),
                "dataset": dataset,
            })
        columns = body.get("columns") or []
        for raw_column in columns if isinstance(columns, list) else []:
            if not isinstance(raw_column, dict):
                continue
            item = _field(raw_column, table=table, dataset=dataset)
            if not item["physical_name"]:
                continue
            field_type = item["type"].lower()
            if any(token in field_type for token in _NUMERIC_TYPES):
                metrics.append(item)
            else:
                dimensions.append(item)
            if any(token in field_type for token in _TIME_TYPES):
                time_fields.append(item)

    suggestions: list[dict[str, str]] = []
    for metric in metrics[:2]:
        dimension = next((item for item in dimensions if item["dataset"] == metric["dataset"]), None)
        dataset = metric["dataset"]
        if dimension:
            query = f"查询数据集 {dataset} 中{metric['label']}按{dimension['label']}的分布和排名"
            label = f"按{dimension['label']}分析{metric['label']}"
        else:
            query = f"查询数据集 {dataset} 中{metric['label']}的汇总和变化"
            label = f"分析{metric['label']}"
        suggestions.append({"label": label, "query": query, "dataset": dataset})
    freshness = time_fields[0] if time_fields else None
    return {
        "version": 1,
        "datasets": datasets,
        "tables": tables,
        "metrics": metrics,
        "dimensions": dimensions,
        "freshness": freshness,
        "suggestions": suggestions[:4],
    }


def metadata_guide_markdown(guide: dict[str, Any]) -> str:
    suggestions = guide.get("suggestions") or []
    if not suggestions:
        return ""
    lines = ["### 可以继续这样分析"]
    for item in suggestions:
        lines.append(f"- [{item['label']}](quick:{item['query']})")
    return "\n".join(lines)


def build_grounded_clarification_queries(
    dataset_menu: str,
    missing_fields: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Build choices from the permission-filtered dataset menu, never free-form names."""
    text = str(dataset_menu or "")
    blocks = re.split(r"(?=^- Dataset:\s*)", text, flags=re.MULTILINE)
    queries: list[str] = []
    for block in blocks:
        dataset_match = re.search(r"^- Dataset:\s*([^\s\[]+)", block, flags=re.MULTILINE)
        if not dataset_match:
            continue
        dataset = dataset_match.group(1).strip()
        display_match = re.search(r"^\s*Display Name:\s*(.+)$", block, flags=re.MULTILINE)
        metrics_match = re.search(r"^\s*Metrics:\s*(.+)$", block, flags=re.MULTILINE)
        tables_match = re.search(r"^\s*Includes Tables:\s*(.+)$", block, flags=re.MULTILINE)
        display = display_match.group(1).strip() if display_match else dataset
        metrics = [item.strip() for item in (metrics_match.group(1).split(",") if metrics_match else []) if item.strip()]
        tables = [item.strip() for item in (tables_match.group(1).split(",") if tables_match else []) if item.strip()]
        if metrics:
            queries.append(f"查询数据集 {dataset}（{display}）中{metrics[0]}的本月汇总")
        elif tables:
            queries.append(f"查询数据集 {dataset}（{display}）中{tables[0]}的最近明细")
        else:
            queries.append(f"说明数据集 {dataset}（{display}）可以分析哪些指标和维度")
        if len(queries) >= 4:
            break
    return tuple(queries)
