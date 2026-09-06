"""Phase 2.1：Few-shot 自愈学习 Qdrant 持久层。

存储"曾经失败→修复成功"的 SQL 经验对，用于 correct_sql 节点召回相似问题的
历史修复方案，辅助 LLM 自愈纠错。

与 column/metric 集合的关键区别：
- 不随知识库 rebuild 重建（它是运行时持续积累的经验，与语义层版本无关）
- 不使用 alias 原子切换（直接读写固定集合名）
- 集合在 lifespan 启动时幂等创建
"""

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
from app.core.log import logger
from app.entities.feedback_entry import FeedbackEntry


class FeedbackQdrantRepository:
    """操作 Few-shot 自愈经验对的 Qdrant 持久层。"""

    # 集合名固定，不随 build_id 变化（经验跨版本积累）
    collection_name = f"{app_config.qdrant.collection_prefix}-feedback"

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self) -> None:
        """幂等创建 feedback 集合（启动时调用）。"""

        if not await self.client.collection_exists(collection_name=self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Few-shot 自愈经验集合已创建: {}", self.collection_name)

    async def upsert(self, entry: FeedbackEntry, embedding: list[float]) -> None:
        """写入或更新一条经验对（id 相同则覆盖，天然去重）。"""

        await self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=entry.id,
                    payload=asdict(entry),
                    vector=embedding,
                )
            ],
        )
        logger.info("Few-shot 经验已回写: id={} question={}", entry.id, entry.question[:50])

    async def search(
        self,
        embedding: list[float],
        score_threshold: float = 0.7,
        limit: int = 3,
        lifecycle: str = "published",
    ) -> list[FeedbackEntry]:
        """按问题语义相似度召回历史修复对。

        score_threshold 默认 0.7（高于 column 的 0.6），因为经验召回要求更高
        精确度——错误的历史经验会误导 LLM，宁可少召回也不滥召回。
        """

        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            score_threshold=score_threshold,
            limit=limit,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="lifecycle",
                        match=MatchValue(value=lifecycle),
                    )
                ]
            ),
        )
        # point.payload 理论上始终非空（我们写入时都带 payload），但类型标注含 None，
        # 这里过滤掉空 payload 的点，避免 ** 解包异常。
        entries: list[FeedbackEntry] = []
        for point in result.points:
            if point.payload:
                entries.append(FeedbackEntry(**point.payload))
        return entries
