"""Memory index HASH parsing with binary embedding vectors."""
import struct
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory_index_service import MemoryIndexService, _vector_to_bytes

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_parse_hash_binary_embedding():
    vec = [0.1, 0.2, 0.3, 0.4]
    raw = {
        b"user_id": b"1",
        b"conversation_id": b"conv-1",
        b"title": "测试".encode("utf-8"),
        b"summary": "摘要内容",
        b"last_active": b"1716880000",
        b"turn_count": b"3",
        b"embedding": _vector_to_bytes(vec),
    }
    parsed = await MemoryIndexService._parse_hash(raw)
    assert parsed["user_id"] == "1"
    assert parsed["conversation_id"] == "conv-1"
    assert parsed["title"] == "测试"
    assert parsed["has_embedding"] is True
    assert len(parsed["_embedding_vec"]) == len(vec)
    for a, b in zip(parsed["_embedding_vec"], vec):
        assert abs(a - b) < 1e-5
    assert "embedding" not in parsed


@pytest.mark.asyncio
async def test_list_summaries_uses_binary_redis_for_hgetall():
    vec = [1.0, 0.0, 0.0, 0.0]
    binary_redis = AsyncMock()
    binary_redis.hgetall = AsyncMock(
        return_value={
            b"user_id": b"9",
            b"conversation_id": b"c1",
            b"title": b"t",
            b"summary": b"s",
            b"last_active": b"100",
            b"turn_count": b"1",
            b"embedding": _vector_to_bytes(vec),
        }
    )
    text_redis = AsyncMock()

    async def scan_iter(match, count=200):
        yield "memory:summary:9:c1"

    text_redis.scan_iter = scan_iter

    with patch(
        "app.services.ai.memory_index_service.get_redis",
        new_callable=AsyncMock,
        return_value=text_redis,
    ), patch(
        "app.services.ai.memory_index_service.get_redis_binary",
        new_callable=AsyncMock,
        return_value=binary_redis,
    ):
        items = await MemoryIndexService.list_summaries("9", limit=10)

    assert len(items) == 1
    assert items[0]["has_embedding"] is True
    binary_redis.hgetall.assert_awaited()


@pytest.mark.asyncio
async def test_search_summaries_uses_redis_knn_when_query_embedding_exists():
    binary_redis = AsyncMock()
    binary_redis.execute_command = AsyncMock(
        return_value=[
            1,
            b"memory:summary:9:c1",
            [
                b"user_id",
                b"9",
                b"conversation_id",
                b"c1",
                b"title",
                "容量规划".encode("utf-8"),
                b"summary",
                "讨论了 Redis 向量检索".encode("utf-8"),
                b"last_active",
                b"1716880000",
                b"turn_count",
                b"2",
                b"score",
                b"0.25",
                b"entities",
                "Redis".encode("utf-8"),
            ],
        ]
    )

    with patch(
        "app.services.ai.memory_index_service.get_redis_binary",
        new_callable=AsyncMock,
        return_value=binary_redis,
    ):
        items = await MemoryIndexService.search_summaries(
            "9", query="Redis", query_embedding=[1.0, 0.0, 0.0, 0.0], limit=3
        )

    assert items == [
        {
            "user_id": "9",
            "conversation_id": "c1",
            "title": "容量规划",
            "summary": "讨论了 Redis 向量检索",
            "last_active": 1716880000,
            "turn_count": 2,
            "has_embedding": True,
            "score": 0.75,
            "entities": "Redis",
        }
    ]
    command = binary_redis.execute_command.await_args.args
    assert command[0] == "FT.SEARCH"
    assert "KNN" in command[2]
    assert "vec" in command
