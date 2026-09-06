import asyncio
from random import random
from typing import Optional

from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import QdrantConfig, app_config


class QdrantClientManager:
    def __init__(self, config: QdrantConfig):
        self.config = config
        self.client: Optional[AsyncQdrantClient] = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init_client(self):
        self.client = AsyncQdrantClient(self._get_url())

    async def close(self):
        await self.client.close()


qdrant_client_manager = QdrantClientManager(app_config.qdrant)

if __name__ == '__main__':
    async def test():
        qdrant_client_manager.init_client()
        client = qdrant_client_manager.client
        collection_name = "my_collection"

        # 如果不存在才创建集合  =》开发
        # if not await client.collection_exists(collection_name):
        #     await client.create_collection(
        #         collection_name=collection_name, # 集合名称
        #         vectors_config=models.VectorParams(
        #             size=1024, # 嵌入向量的维度
        #             distance=models.Distance.COSINE  # 余弦相似度匹配算法
        #         )
        #     )

        # 如果存在集合，则删除集合，每次创建新的集合  =》为了方便测试
        if await client.collection_exists(collection_name):
            await client.delete_collection(collection_name)
        await client.create_collection(
            collection_name=collection_name, # 集合名称
            vectors_config=models.VectorParams(
                size=1024, # 嵌入向量的维度
                distance=models.Distance.COSINE  # 余弦相似度匹配算法
            )
        )

        # 批量插入多个向量
        await client.upsert(
            collection_name=collection_name,
            points=[
                models.PointStruct(
                    id=i,
                    payload={
                        "color": "red" if i % 2 == 0 else "blue",
                    },
                    vector=[random() for _ in range(1024)],
                )
                for i in range(100)
            ],
        )

        # 搜索匹配的向量
        result = await client.query_points(
            collection_name=collection_name,
            query=[random() for _ in range(1024)],
            limit=5,
            score_threshold=0.7,  # 向量相似度阈值，低于该阈值的向量将被忽略
            query_filter=models.Filter( # 根据携带的数据payload进行过滤
                must=[models.FieldCondition(key="color", match=models.MatchValue(value="red"))]
            ),
        )
        print(result.points)

        for point in result.points:
            print(point.payload)


        await qdrant_client_manager.close()

    asyncio.run(test())