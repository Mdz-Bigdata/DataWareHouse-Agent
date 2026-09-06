from __future__ import annotations

from dataclasses import asdict

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
from app.entities.semantic_term import SemanticTerm


class SemanticTermQdrantRepository:
    collection_name = f"{app_config.qdrant.collection_prefix}-semantic-terms"

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

    async def upsert(self, term: SemanticTerm, embedding: list[float]) -> None:
        await self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=term.id,
                    vector=embedding,
                    payload=asdict(term),
                )
            ],
        )

    async def search(
        self,
        embedding: list[float],
        *,
        domain: str,
        datasource: str,
        score_threshold: float = 0.65,
        limit: int = 5,
    ) -> list[SemanticTerm]:
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            query_filter=Filter(
                must=[
                    FieldCondition(key="domain", match=MatchValue(value=domain)),
                    FieldCondition(key="datasource", match=MatchValue(value=datasource)),
                    FieldCondition(key="status", match=MatchValue(value="published")),
                ]
            ),
            score_threshold=score_threshold,
            limit=limit,
        )
        return [
            SemanticTerm(**point.payload) for point in result.points if point.payload is not None
        ]
