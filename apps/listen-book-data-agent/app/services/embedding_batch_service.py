"""Bound embedding request batches to the local TEI backend's safe capacity."""

from __future__ import annotations

import asyncio

SAFE_EMBEDDING_BATCH_SIZE = 4


async def embed_documents_batched(
    embedding_client: object,
    texts: list[str],
    *,
    batch_size: int = SAFE_EMBEDDING_BATCH_SIZE,
) -> list[list[float]]:
    if batch_size < 1:
        raise ValueError("embedding batch_size 必须大于 0")
    if not texts:
        return []
    embed_documents = getattr(embedding_client, "aembed_documents", None)
    if embed_documents is None:
        return await asyncio.gather(
            *(embedding_client.aembed_query(text) for text in texts)
        )
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        embeddings.extend(
            await embed_documents(texts[start : start + batch_size])
        )
    return embeddings
