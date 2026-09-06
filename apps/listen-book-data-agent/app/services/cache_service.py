"""Phase 4.1：缓存服务。

封装"先查 Redis → miss 则查源 → 回写 Redis"的标准模式。
Redis 不可用时透明降级为直查源（调用方无感知）。

缓存对象：
- 语义层元数据：table_infos / metric_infos / relationships（按 build_id + 实体类型）
- 高频查询结果：question → SQL 结果（按 user_id + question 哈希）
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.clients.redis_client_manager import redis_client_manager
from app.conf.app_config import app_config
from app.core.log import logger


def _meta_key(build_id: str, entity: str) -> str:
    """语义层元数据缓存键。entity 如 'tables' / 'metrics' / 'relationships'。"""

    return f"meta:{build_id}:{entity}"


def _query_key(user_id: str | None, question: str) -> str:
    """查询结果缓存键。用 question 的 sha256 避免长文本作键。"""

    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    return f"query:{user_id or 'anonymous'}:{question_hash}"


async def get_or_load_meta(
    build_id: str,
    entity: str,
    loader: Callable[[], Awaitable[Any]],
) -> Any:
    """语义层元数据缓存：先查 Redis，miss 则调 loader 加载并回写。

    loader 是异步函数，返回可 JSON 序列化的数据（list/dict）。
    Redis 不可用时直接调 loader，不影响正确性。
    """

    if not redis_client_manager.available:
        return await loader()
    key = _meta_key(build_id, entity)
    cached = await redis_client_manager.get_json(key)
    if cached is not None:
        logger.debug("缓存命中（元数据）: {}", key)
        return cached
    data = await loader()
    await redis_client_manager.set_json(key, data, ttl=app_config.redis.meta_ttl_seconds)
    return data


async def get_or_load_query_result(
    user_id: str | None,
    question: str,
    loader: Callable[[], Awaitable[dict]],
) -> dict | None:
    """查询结果缓存：先查 Redis，miss 则调 loader 加载并回写。

    返回 None 表示不走缓存（如 loader 内部决定不缓存）。
    查询结果结构同 QueryService.query_sync 的返回。
    """

    if not redis_client_manager.available:
        return None
    key = _query_key(user_id, question)
    cached = await redis_client_manager.get_json(key)
    if cached is not None:
        logger.debug("缓存命中（查询结果）: {}", key[:60])
        return cached
    return None


async def cache_query_result(
    user_id: str | None,
    question: str,
    result: dict,
) -> None:
    """回写查询结果到缓存（仅成功结果才缓存）。"""

    if not redis_client_manager.available:
        return
    key = _query_key(user_id, question)
    await redis_client_manager.set_json(key, result, ttl=app_config.redis.query_ttl_seconds)


async def invalidate_meta_cache(build_id: str) -> None:
    """使指定 build_id 的所有元数据缓存失效（知识重建后调用）。"""

    if not redis_client_manager.available:
        return
    # 删除该 build_id 下所有 entity 的缓存
    for entity in ("tables", "metrics", "relationships", "columns"):
        await redis_client_manager.delete(_meta_key(build_id, entity))
    logger.info("已失效元数据缓存: build_id={}", build_id)


async def invalidate_query_cache_for_user(user_id: str) -> None:
    """使用户的查询结果缓存失效（语义层变化或手动清除时调用）。

    注意：Redis SCAN+DELETE 在大库上有性能开销，这里采用保守策略——
    仅在知识重建时调用 invalidate_meta_cache，查询缓存自然过期即可。
    本函数预留给未来按需调用。
    """

    # 当前实现为空，避免 SCAN 全库。查询缓存靠 TTL 自然过期。
    pass
