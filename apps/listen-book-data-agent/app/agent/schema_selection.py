"""Safe schema selection and relationship path completion helpers."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable
from typing import TypeVar

from app.entities.column_info import ColumnInfo
from app.entities.relationship_info import RelationshipInfo

T = TypeVar("T")


def without_sensitive_columns(columns: Iterable[ColumnInfo]) -> list[ColumnInfo]:
    """Deduplicate columns while ensuring PII cannot reach the SQL prompt."""

    selected: dict[str, ColumnInfo] = {}
    for column in columns:
        if not column.sensitive:
            selected.setdefault(column.id, column)
    return list(selected.values())


def shortest_relationship_paths(
    table_ids: Iterable[str], relationships: Iterable[RelationshipInfo]
) -> list[RelationshipInfo]:
    """Return a minimal union of FK/virtual paths connecting selected tables."""

    ordered_tables = list(dict.fromkeys(table_ids))
    if len(ordered_tables) < 2:
        return []
    adjacency: dict[str, list[tuple[str, RelationshipInfo]]] = {}
    for relationship in relationships:
        adjacency.setdefault(relationship.source_table, []).append(
            (relationship.target_table, relationship)
        )
        adjacency.setdefault(relationship.target_table, []).append(
            (relationship.source_table, relationship)
        )

    selected: dict[str, RelationshipInfo] = {}
    root = ordered_tables[0]
    for target in ordered_tables[1:]:
        path = _find_path(root, target, adjacency)
        for relationship in path:
            selected.setdefault(relationship.id, relationship)
    return list(selected.values())


def relationship_condition_column(relationship: RelationshipInfo) -> str | None:
    """Extract a virtual relationship discriminator from its stored condition."""

    if not relationship.condition:
        return None
    pattern = rf"\b{re.escape(relationship.source_table)}\.([a-zA-Z_][a-zA-Z0-9_]*)\b"
    match = re.search(pattern, relationship.condition)
    return match.group(1) if match else None


def _find_path(
    source: str,
    target: str,
    adjacency: dict[str, list[tuple[str, RelationshipInfo]]],
) -> list[RelationshipInfo]:
    queue = deque([(source, [])])
    visited = {source}
    while queue:
        current, path = queue.popleft()
        if current == target:
            return path
        for neighbour, relationship in adjacency.get(current, []):
            if neighbour not in visited:
                visited.add(neighbour)
                queue.append((neighbour, [*path, relationship]))
    return []


# ==================== Phase 2.3：Schema Linking 精排 ====================


def filter_island_tables(
    table_ids: list[str],
    relationships: Iterable[RelationshipInfo],
) -> list[str]:
    """孤岛过滤：丢弃与任何 relationship 无连通的表（防幻觉）。

    背景：召回阶段可能命中一些无法通过 JOIN 连接到主分析链路的维度表。
    若把它们放进 LLM 上下文，模型可能编造不存在的 JOIN 条件（Phase 1.1
    虽能拦下，但浪费 token 且增加幻觉面）。这里在进入 prompt 前主动丢弃。

    策略：构建 relationship 涉及的表的邻接图，只保留出现在该图中的表。
    单表查询（无 relationship）原样返回，不误杀。
    """

    if not table_ids:
        return []
    connected_tables: set[str] = set()
    for rel in relationships:
        connected_tables.add(rel.source_table)
        connected_tables.add(rel.target_table)
    # 无任何 relationship 时，无法判定连通性，保守原样返回（不误杀）
    if not connected_tables:
        return list(table_ids)
    # 保留：既在候选表里，又在连通图里的表
    return [tid for tid in table_ids if tid in connected_tables]


def score_by_literal_match(  # noqa: UP047 保留传统 TypeVar 写法，避免 ruff/black 对 PEP695 的格式分歧
    candidates: Iterable[T],
    query: str,
    name_of: Callable[[T], str],
    alias_of: Callable[[T], Iterable[str]],
    description_of: Callable[[T], str],
) -> list[tuple[T, int]]:
    """字面提权：query 命中候选对象的文本则加分，返回 (对象, 分数) 列表。

    评分权重：name 命中 +10（最强信号），alias 命中 +5，description 命中 +2。
    未命中的对象分数为 0，仍保留在结果里（只重排不过滤），由调用方决定是否截断。

    用途：recall_column / recall_metric 召回后，把 query 字面提到的列/指标
    提到前面，提升 LLM 注意力分配的准确性。
    """

    query_lower = query.lower()
    scored: list[tuple[T, int]] = []
    for candidate in candidates:
        score = 0
        name = name_of(candidate).lower()
        if name and name in query_lower:
            score += 10
        for alias in alias_of(candidate):
            alias_lower = alias.lower()
            if alias_lower and len(alias_lower) >= 2 and alias_lower in query_lower:
                score += 5
        desc = description_of(candidate).lower()
        if desc and len(desc) >= 2 and desc in query_lower:
            score += 2
        scored.append((candidate, score))
    # 按分数降序，分数相同保持原顺序（稳定排序）
    return sorted(scored, key=lambda item: -item[1])
