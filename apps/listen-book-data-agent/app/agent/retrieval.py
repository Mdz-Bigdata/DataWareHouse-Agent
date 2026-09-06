"""Shared resilient retrieval helpers used by schema and metric recall nodes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import TypeVar

from app.entities.column_info import ColumnInfo
from app.services.embedding_batch_service import embed_documents_batched

T = TypeVar("T")


def recall_terms(
    keywords: Iterable[str],
    analysis_plan: dict | None,
    original_query: str | None = None,
) -> list[str]:
    """优先保留完整问题和规范化计划，避免关键复合词被 top-k 截断。"""

    terms: list[str] = []
    if original_query:
        terms.append(original_query)
    if analysis_plan:
        terms.extend(analysis_plan.get("metric_hints", []))
        terms.extend(analysis_plan.get("filters", []))
        terms.extend(analysis_plan.get("dimensions", []))
    terms.extend(keywords)
    return list(dict.fromkeys(term.strip() for term in terms if term and term.strip()))[:16]


async def batched_vector_search(
    *,
    terms: list[str],
    embedding_client,
    search: Callable[[list[float]], object],
) -> list[T]:
    """Embed all terms in one request and fan out vector searches concurrently."""

    if not terms:
        return []
    embeddings = await embed_documents_batched(embedding_client, terms)
    groups = await asyncio.gather(*(search(embedding) for embedding in embeddings))
    return _deduplicate(item for group in groups for item in group)


def lexical_rank(
    candidates: Iterable[T], terms: Iterable[str], text_of: Callable[[T], str], limit: int = 12
) -> list[T]:
    """Metadata-only fallback when an embedding or vector service is unavailable."""

    normalized_terms = [term.lower() for term in terms if len(term.strip()) >= 2]
    scored: list[tuple[int, str, T]] = []
    for candidate in candidates:
        text = text_of(candidate).lower()
        score = sum(text.count(term) * max(1, len(term)) for term in normalized_terms)
        if score:
            scored.append((score, getattr(candidate, "id", ""), candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def allowed_columns(columns: Iterable[ColumnInfo]) -> list[ColumnInfo]:
    return [column for column in _deduplicate(columns) if not column.sensitive]


def merge_retrieval_results(*groups: Iterable[T], limit: int) -> list[T]:
    """按组优先级稳定合并向量与词法结果并去重。"""

    return _deduplicate(item for group in groups for item in group)[:limit]


def _deduplicate(items: Iterable[T]) -> list[T]:
    values: dict[str, T] = {}
    for item in items:
        values.setdefault(item.id, item)
    return list(values.values())
