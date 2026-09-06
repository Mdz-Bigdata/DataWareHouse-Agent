"""联邦查询结果列名 -> 中文业务术语映射（供 Markdown 表格与 synthesis 使用）。"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover - optional at import time
    sqlglot = None
    exp = None

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def looks_chinese(text: Any) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def is_latin_like_header(name: str) -> bool:
    raw = str(name or "").strip()
    if not raw or looks_chinese(raw):
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw))


def extract_column_term_map_from_schema(schema_output: str) -> dict[str, str]:
    """从 get_dataset_schema / 合并 Schema YAML 文本解析 physical_name -> term。"""
    term_map: dict[str, str] = {}
    current_col: str | None = None
    in_columns = False
    columns_indent = 0

    for raw_line in str(schema_output or "").splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())

        if re.match(r"columns:\s*$", stripped, flags=re.IGNORECASE):
            in_columns = True
            columns_indent = indent
            current_col = None
            continue

        if in_columns and indent <= columns_indent and not stripped.startswith("-") and "columns:" not in stripped.lower():
            in_columns = False
            current_col = None

        if not in_columns:
            continue

        item_match = re.match(
            r"-\s*(?:name|physical_name):\s*([A-Za-z_][\w.$]*)\s*$",
            stripped,
            flags=re.IGNORECASE,
        )
        if item_match:
            current_col = item_match.group(1).strip()
            continue

        term_match = re.match(r"term:\s*(.+?)\s*$", stripped, flags=re.IGNORECASE)
        if term_match and current_col:
            term = term_match.group(1).strip().strip('"').strip("'")
            if term:
                term_map[current_col.lower()] = term
            continue

        if stripped.startswith("- ") and not item_match:
            current_col = None

    return term_map


def merge_column_term_maps(*maps: Mapping[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for mapping in maps:
        if not mapping:
            continue
        for key, value in mapping.items():
            term = str(value or "").strip()
            if not term:
                continue
            merged[str(key).lower()] = term
    return merged


def resolve_column_display_name(raw_name: str, term_map: Mapping[str, str]) -> str:
    raw = str(raw_name or "").strip()
    if not raw:
        return raw
    if looks_chinese(raw):
        return raw
    term = str(term_map.get(raw.lower()) or term_map.get(raw) or "").strip()
    if term and looks_chinese(term):
        return term
    if term and term.lower() != raw.lower() and not is_latin_like_header(term):
        return term
    return raw


def build_column_label_map(
    column_names: Iterable[str],
    term_map: Mapping[str, str],
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for name in column_names:
        raw = str(name or "").strip()
        if not raw:
            continue
        display = resolve_column_display_name(raw, term_map)
        if display != raw:
            labels[raw] = display
    return labels


def extract_alias_term_hints_from_join_sql(
    sql: str,
    term_map: Mapping[str, str],
) -> dict[str, str]:
    """从 memory_join 的 `expr AS alias` 推断 alias -> 中文 term。"""
    if not sql or not term_map or sqlglot is None or exp is None:
        return {}
    hints: dict[str, str] = {}
    try:
        parsed = sqlglot.parse_one(str(sql).strip(), read="duckdb")
    except Exception:
        return hints
    if not isinstance(parsed, exp.Select):
        return hints

    for expression in parsed.expressions:
        alias = str(getattr(expression, "alias", None) or "").strip()
        if not alias or looks_chinese(alias):
            continue
        columns = list(expression.find_all(exp.Column))
        if not columns:
            continue
        physical = str(columns[-1].name or "").strip()
        if not physical:
            continue
        term = str(term_map.get(physical.lower()) or "").strip()
        if term and looks_chinese(term):
            hints[alias.lower()] = term
    return hints


def format_column_label_guide(
    term_map: Mapping[str, str],
    column_names: Iterable[str],
    *,
    label_map: Mapping[str, str] | None = None,
    max_lines: int = 40,
) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    labels = label_map or {}
    for name in column_names:
        raw = str(name or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        display = labels.get(raw) or resolve_column_display_name(raw, term_map)
        if display != raw:
            lines.append(f"- {raw} -> {display}")
        elif is_latin_like_header(raw):
            lines.append(f"- {raw} -> （请在输出表格中使用中文表头，勿保留英文/拼音列名）")
        if len(lines) >= max_lines:
            break
    if not lines:
        return ""
    return "\n".join(lines)


async def load_column_term_map_for_datasets(session: Any, dataset_names: Iterable[str]) -> dict[str, str]:
    """从元数据库加载指定 datasets 下所有字段 physical_name -> term。"""
    names = [str(name).strip() for name in dataset_names if str(name or "").strip()]
    if not names:
        return {}

    from sqlalchemy import select

    from app.models.metadata import MetaColumn, MetaDataset, MetaTable

    stmt = (
        select(MetaColumn.physical_name, MetaColumn.term)
        .join(MetaTable, MetaTable.id == MetaColumn.table_id)
        .join(MetaDataset, MetaDataset.id == MetaTable.dataset_id)
        .where(MetaDataset.name.in_(names))
    )
    result = await session.execute(stmt)
    term_map: dict[str, str] = {}
    for physical_name, term in result.all():
        physical = str(physical_name or "").strip()
        label = str(term or "").strip()
        if physical and label:
            term_map[physical.lower()] = label
    return term_map
