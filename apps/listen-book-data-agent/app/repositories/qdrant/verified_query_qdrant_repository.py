from __future__ import annotations

import uuid
from dataclasses import asdict, replace

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.conf.app_config import app_config
from app.entities.verified_query import VerifiedQueryExample


class VerifiedQueryQdrantRepository:
    """Vector index containing published, version-scoped Query Set examples."""

    collection_name = f"{app_config.qdrant.collection_prefix}-verified-queries"

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self) -> None:
        if not await self.client.collection_exists(collection_name=self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=Distance.COSINE,
                ),
            )

    async def upsert_many(
        self,
        examples: list[VerifiedQueryExample],
        embeddings: list[list[float]],
    ) -> None:
        if len(examples) != len(embeddings):
            raise ValueError("可信案例与向量数量不一致")
        if not examples:
            return
        await self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"query-set:{example.query_set_id}:{example.revision_id}",
                        )
                    ),
                    vector=embedding,
                    payload={key: value for key, value in asdict(example).items() if key != "score"},
                )
                for example, embedding in zip(examples, embeddings, strict=True)
            ],
        )

    async def search(
        self,
        embedding: list[float],
        *,
        query_set_id: str,
        domain: str,
        datasource: str,
        dialect: str,
        score_threshold: float = 0.72,
        limit: int = 3,
    ) -> list[VerifiedQueryExample]:
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            query_filter=Filter(
                must=[
                    FieldCondition(key="query_set_id", match=MatchValue(value=query_set_id)),
                    FieldCondition(key="domain", match=MatchValue(value=domain)),
                    FieldCondition(key="datasource", match=MatchValue(value=datasource)),
                    FieldCondition(key="dialect", match=MatchValue(value=dialect)),
                ]
            ),
            score_threshold=score_threshold,
            limit=limit,
        )
        examples: list[VerifiedQueryExample] = []
        for point in result.points:
            if point.payload is not None:
                examples.append(
                    replace(
                        VerifiedQueryExample(**point.payload),
                        score=float(point.score),
                    )
                )
        return examples
