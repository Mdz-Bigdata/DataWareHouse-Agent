"""Daily memory summary rollup behavior."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory_index_service import _vector_to_bytes

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_refresh_for_date_writes_daily_summary_hash():
    from app.services.ai.daily_summary_service import DailySummaryService

    redis = AsyncMock()
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    day_ts = 1716854400
    session_items = [
        {
            "conversation_id": "conv-1",
            "title": "记忆设计",
            "summary": "讨论 session summary 和 daily summary。",
            "last_active": day_ts + 3600,
            "turn_count": 3,
            "key_facts": ["一个会话一条 summary"],
            "decisions": ["新增 daily 层"],
            "open_items": [],
            "entities": ["Redis"],
        }
    ]
    meta = {
        "title": "今日记忆设计",
        "summary": "今天围绕记忆管理做了 session 与 daily 分层设计。",
        "topics": ["记忆管理"],
        "decisions": ["新增 daily 层"],
        "open_items": [],
        "entities": ["Redis"],
    }

    with patch(
        "app.services.ai.daily_summary_service.MemoryIndexService.list_summaries",
        new_callable=AsyncMock,
        return_value=session_items,
    ), patch(
        "app.services.ai.daily_summary_service.ConversationSummarizer.summarize_daily",
        new_callable=AsyncMock,
        return_value=meta,
    ), patch(
        "app.services.ai.daily_summary_service.EmbeddingClient.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1, 0.2],
    ), patch(
        "app.services.ai.daily_summary_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ), patch(
        "app.services.ai.daily_summary_service.MemoryIndexService.ensure_index",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.ai.daily_summary_service.MemoryIndexService.summary_ttl_seconds",
        new_callable=AsyncMock,
        return_value=86400,
    ):
        result = await DailySummaryService.refresh_for_date("7", "2024-05-28")

    assert result["refreshed"] is True
    redis.hset.assert_awaited_once()
    key = redis.hset.await_args.args[0]
    mapping = redis.hset.await_args.kwargs["mapping"]
    assert key == "memory:summary:daily:7:2024-05-28"
    assert mapping["summary_type"] == "daily"
    assert mapping["conversation_id"] == "daily:2024-05-28"
    assert mapping["summary"] == meta["summary"]
    assert mapping["embedding"] == _vector_to_bytes([0.1, 0.2])
