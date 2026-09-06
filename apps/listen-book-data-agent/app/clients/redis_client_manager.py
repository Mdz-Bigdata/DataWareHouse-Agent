"""Phase 4.1：Redis 缓存客户端管理器。

设计原则：Redis 是加速层，不是必需依赖。所有操作失败时静默降级（返回 None
或跳过），绝不阻断主查询流程。这样开发环境（无 Redis）和生产环境（Redis 故障）
都能正常服务。

用法：
    await redis_client_manager.init_client()  # lifespan 启动时
    await redis_client_manager.set("key", "value", ttl=600)
    value = await redis_client_manager.get("key")  # 故障时返回 None
"""

from __future__ import annotations

import contextlib
from typing import Any

from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError

from app.conf.app_config import app_config
from app.core.log import logger


class RedisClientManager:
    """Redis 异步客户端管理器（带降级容错）。"""

    def __init__(self):
        self.client: Redis | None = None
        self._available = False

    def init_client(self) -> None:
        """初始化 Redis 连接。连接失败不抛异常，标记为不可用。"""

        password = app_config.redis.password or None
        try:
            self.client = from_url(
                f"redis://{app_config.redis.host}:{app_config.redis.port}/{app_config.redis.db}",
                password=password,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=False,
            )
            self._available = True
            logger.info(
                "Redis 客户端已初始化: {}:{}",
                app_config.redis.host,
                app_config.redis.port,
            )
        except Exception as exc:
            self._available = False
            self.client = None
            logger.warning("Redis 初始化失败，缓存降级为直查: {}", exc)

    async def close(self) -> None:
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.aclose()
            self.client = None
            self._available = False

    @property
    def available(self) -> bool:
        """Redis 是否可用（初始化成功且未发生不可恢复故障）。"""

        return self._available and self.client is not None

    async def get(self, key: str) -> str | None:
        """获取缓存值。不可用或异常时返回 None（调用方走直查）。"""

        if not self.available or self.client is None:
            return None
        client = self.client
        try:
            result = await client.get(key)
            return str(result) if result is not None else None
        except RedisError:
            logger.warning("Redis GET 失败，降级直查: key={}", key[:80])
            return None
        except Exception:
            logger.warning("Redis GET 异常，降级直查", exc_info=True)
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """写入缓存。不可用或异常时返回 False（不影响主流程）。"""

        if not self.available or self.client is None:
            return False
        client = self.client
        try:
            if ttl and ttl > 0:
                await client.set(key, value, ex=ttl)
            else:
                await client.set(key, value)
            return True
        except RedisError:
            logger.warning("Redis SET 失败，跳过缓存: key={}", key[:80])
            return False
        except Exception:
            logger.warning("Redis SET 异常，跳过缓存", exc_info=True)
            return False

    async def get_json(self, key: str) -> Any | None:
        """获取 JSON 缓存值并反序列化。失败返回 None。"""

        import json

        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """序列化为 JSON 后写入缓存。"""

        import json

        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return False
        return await self.set(key, raw, ttl)

    async def delete(self, key: str) -> bool:
        """删除缓存。失败不影响主流程。"""

        if not self.available or self.client is None:
            return False
        client = self.client
        try:
            await client.delete(key)
            return True
        except Exception:
            return False


# 全局单例
redis_client_manager = RedisClientManager()
