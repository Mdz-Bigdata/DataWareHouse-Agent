from dataclasses import asdict

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from app.conf.app_config import app_config
from app.core.log import logger
from app.entities.column_info import ColumnInfo


class ColumnQdrantRepository:
    """操作字段信息Qdrant向量库持久层"""

    alias_name = f"{app_config.qdrant.collection_prefix}-column"

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection_name: str | None = None,
    ):
        self.client = client
        self.coll_name = collection_name or self.alias_name

    async def ensure_collection(self):
        """
            判断向量集合是否存在，不存在则新增
        :return:
        """
        if not await self.client.collection_exists(collection_name=self.coll_name):
            result = await self.client.create_collection(
                collection_name=self.coll_name,
                vectors_config=VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=Distance.COSINE
                )
            )
            logger.info(f"创建向量集合成功:{result}")

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

    async def upsert(self, ids: list[str], payloads: list[ColumnInfo], embeddings: list[list[float]], batch_size=20):
        # 1.利用zip函数按索引"打包"为元祖列表 [(id,元数据,向量值)]
        zipped = list(zip(ids, payloads, embeddings))
        # 2. 分批次插入向量数据
        for i in range(0, len(zipped), batch_size):
            batch = zipped[i:i + batch_size]
            points = [PointStruct(
                id=id,
                payload=asdict(payload),  # 必须是字典结构
                vector=embedding
            ) for id, payload, embedding in batch]
            await self.client.upsert(
                collection_name=self.coll_name,
                points=points
            )

    async def search(self, embedding: list[float], score_threshold: float = 0.6, limit: int = 10) -> list[ColumnInfo]:
        result = await self.client.query_points(collection_name=self.coll_name, query=embedding,
                                                score_threshold=score_threshold, limit=limit)
        return [ColumnInfo(
            **point.payload # qdrant中存储payload是字典{id:x}
        ) for point in result.points]
