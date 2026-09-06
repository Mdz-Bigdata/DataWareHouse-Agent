"""Daily rollup for cross-session memory summaries."""
import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.redis import get_redis, get_redis_binary
from app.services.ai.conversation_summarizer import ConversationSummarizer
from app.services.ai.embedding_client import EmbeddingClient
from app.services.ai.memory_index_service import (
    MemoryIndexService,
    _vector_to_bytes,
)

logger = logging.getLogger(__name__)

DAILY_SUMMARY_KEY_PREFIX = "memory:summary:daily:"


def _daily_key(user_id: str, day: str) -> str:
    return f"{DAILY_SUMMARY_KEY_PREFIX}{user_id}:{day}"


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
        return [text]
    return [str(value)]


class DailySummaryService:
    @staticmethod
    def today() -> str:
        return date.today().isoformat()

    @staticmethod
    def _item_day(item: Dict[str, Any]) -> Optional[str]:
        ts = int(item.get("last_active") or 0)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts).date().isoformat()

    @staticmethod
    def _daily_embedding_text(meta: Dict[str, Any]) -> str:
        parts = [
            meta.get("title") or "",
            meta.get("summary") or "",
            " ".join(_as_list(meta.get("topics"))),
            " ".join(_as_list(meta.get("decisions"))),
            " ".join(_as_list(meta.get("open_items"))),
            " ".join(_as_list(meta.get("entities"))),
        ]
        return "\n".join(p for p in parts if p)

    @staticmethod
    async def refresh_for_date(user_id: str, day: Optional[str] = None) -> Dict[str, Any]:
        uid = str(user_id)
        target_day = day or DailySummaryService.today()
        sessions = await MemoryIndexService.list_summaries(uid, limit=200)
        day_sessions = [
            item
            for item in sessions
            if item.get("summary_type", "session") == "session"
            and DailySummaryService._item_day(item) == target_day
        ]
        if not day_sessions:
            return {"refreshed": False, "reason": "empty_day", "date": target_day}

        meta = await ConversationSummarizer.summarize_daily(day_sessions)
        embedding = None
        try:
            embedding = await EmbeddingClient.embed_text(
                DailySummaryService._daily_embedding_text(meta)
            )
        except Exception as e:
            logger.warning("[DailySummary] Embedding failed: %s", e)

        redis = await get_redis()
        if not redis:
            return {"refreshed": False, "reason": "redis_unavailable", "date": target_day}

        now = int(datetime.now().timestamp())
        mapping = {
            "user_id": uid,
            "conversation_id": f"daily:{target_day}",
            "summary_type": "daily",
            "date": target_day,
            "title": meta.get("title") or target_day,
            "summary": meta.get("summary") or "",
            "topics": json.dumps(_as_list(meta.get("topics")), ensure_ascii=False),
            "decisions": json.dumps(_as_list(meta.get("decisions")), ensure_ascii=False),
            "open_items": json.dumps(_as_list(meta.get("open_items")), ensure_ascii=False),
            "entities": json.dumps(_as_list(meta.get("entities")), ensure_ascii=False),
            "last_active": str(now),
            "turn_count": str(sum(int(i.get("turn_count") or 0) for i in day_sessions)),
        }
        if embedding:
            mapping["embedding"] = _vector_to_bytes(embedding)

        key = _daily_key(uid, target_day)
        await redis.hset(key, mapping=mapping)
        await redis.expire(key, await MemoryIndexService.summary_ttl_seconds())
        await MemoryIndexService.ensure_index()
        logger.info("[DailySummary] Updated daily summary for user=%s date=%s", uid, target_day)
        return {"refreshed": True, "date": target_day, "sessions": len(day_sessions)}

    @staticmethod
    async def get_daily_summary(user_id: str, day: str) -> Optional[Dict[str, Any]]:
        redis = await get_redis_binary()
        if not redis:
            return None
        raw = await redis.hgetall(_daily_key(str(user_id), day))
        if not raw:
            return None
        return await MemoryIndexService._parse_hash(raw)

    @staticmethod
    async def list_daily_summaries(
        user_id: str,
        keyword: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        redis = await get_redis()
        if not redis:
            return []
        items: List[Dict[str, Any]] = []
        async for key in redis.scan_iter(match=f"{DAILY_SUMMARY_KEY_PREFIX}{user_id}:*", count=200):
            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
            raw = await DailySummaryService.get_daily_summary(user_id, key_str.rsplit(":", 1)[-1])
            if raw:
                items.append(raw)

        kw = (keyword or "").strip().lower()
        if kw:
            items = [
                i
                for i in items
                if kw in (i.get("summary") or "").lower()
                or kw in (i.get("title") or "").lower()
                or kw in (i.get("date") or "").lower()
            ]
        if date_from:
            items = [i for i in items if (i.get("date") or "") >= date_from]
        if date_to:
            items = [i for i in items if (i.get("date") or "") <= date_to]

        items.sort(key=lambda x: x.get("date") or "", reverse=True)
        return items[:limit]

    @staticmethod
    async def delete_daily_summary(user_id: str, day: str) -> None:
        redis = await get_redis()
        if not redis:
            return
        await redis.delete(_daily_key(str(user_id), day))

    @staticmethod
    async def delete_all_for_user(user_id: str) -> int:
        redis = await get_redis()
        if not redis:
            return 0
        uid = str(user_id)
        count = 0
        async for key in redis.scan_iter(match=f"{DAILY_SUMMARY_KEY_PREFIX}{uid}:*", count=200):
            await redis.delete(key)
            count += 1
        return count
