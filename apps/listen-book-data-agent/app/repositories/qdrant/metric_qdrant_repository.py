from dataclasses import asdict

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from app.conf.app_config import app_config
from app.entities.metric_info import MetricInfo


class MetricQdrantRepository:
    alias_name = f"{app_config.qdrant.collection_prefix}-metric"

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection_name: str | None = None,
    ):
        self.client = client
        self.coll_name = collection_name or self.alias_name

    async def ensure_collection(self):
        if not await self.client.collection_exists(collection_name=self.coll_name):
            await self.client.create_collection(
                collection_name=self.coll_name,
                vectors_config=VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=Distance.COSINE
                )
            )

    async def get_alias_target(self, alias_name: str | None = None) -> str | None:
        alias = alias_name or self.alias_name
        response = await self.client.get_aliases()
        for item in response.aliases:
            if item.alias_name == alias:
                return item.collection_name
        return None

    async def set_alias(
        self,
        target_collection: str | None = None,
        alias_name: str | None = None,
    ) -> None:
        alias = alias_name or self.alias_name
        target = self.coll_name if target_collection is None else target_collection
        current = await self.get_alias_target(alias)
        if current == target:
            return
        operations: list[models.CreateAliasOperation | models.DeleteAliasOperation] = []
        if current:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=alias)
                )
            )
        if target:
            operations.append(
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=target,
                        alias_name=alias,
                    )
                )
            )
        if operations:
            await self.client.update_collection_aliases(operations)

    async def delete_collection(self) -> None:
        if await self.client.collection_exists(self.coll_name):
            await self.client.delete_collection(self.coll_name)

    async def upsert(self, ids:list[str], payloads:list[MetricInfo], embeddings:list[list[float]], batch_size:int=20):
        # [(向量ID,业务数据,向量值),()]
        zipped = list(zip(ids, payloads, embeddings))
        #分批次处理
        for i in  range(0, len(zipped), batch_size):
            batch = zipped[i:i+batch_size]
            points = [PointStruct(
                id=id,
                vector=embedding,
                payload=asdict(payload)
            ) for id,payload,embedding in batch]
            await self.client.upsert(collection_name=self.coll_name, points=points)

    async def search(self, embedding:list[float], score_threshold: float = 0.6, limit: int = 10):
        result = await self.client.query_points(
            collection_name=self.coll_name,
            query=embedding,
            score_threshold=score_threshold,
            limit=limit
        )
        return [MetricInfo(**point.payload) for point in result.points]
