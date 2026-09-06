"""Redis Stack / RediSearch vector index connectivity probe for memory service."""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None
_CACHE_TTL = 30.0

_PROBE_INDEX = "idx:memory:_vector_health_probe"
_PROBE_PREFIX = "memory:_vector_health_probe:"


class RedisVectorHealthService:
    @staticmethod
    def _check_item(name: str, passed: bool, message: str) -> Dict[str, Any]:
        return {"name": name, "passed": passed, "message": message}

    @staticmethod
    async def check(force: bool = False) -> Dict[str, Any]:
        global _CACHE
        now = time.time()
        if not force and _CACHE and (now - _CACHE[0]) < _CACHE_TTL:
            return _CACHE[1]

        hints: List[str] = [
            "请使用 Redis Stack（或已加载 RediSearch 模块的 Redis 7+）。",
            "在 .env 中将 REDIS_DB 设为 0（RediSearch 索引仅支持 database 0）。",
            "示例：docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest",
            "修改配置后重启后端服务，再在本页点击「重新检测」。",
        ]

        result: Dict[str, Any] = {
            "ok": False,
            "message": "",
            "hints": hints,
            "redis_enabled": bool(settings.REDIS_ENABLE),
            "redis_host": settings.REDIS_HOST,
            "redis_port": settings.REDIS_PORT,
            "redis_db": settings.REDIS_DB,
            "checks": [],
        }
        checks: List[Dict[str, Any]] = result["checks"]

        if not settings.REDIS_ENABLE:
            checks.append(
                RedisVectorHealthService._check_item(
                    "redis_enabled", False, "REDIS_ENABLE 未开启"
                )
            )
            result["message"] = "Redis 未启用，请在环境变量中设置 REDIS_ENABLE=true"
            _CACHE = (now, result)
            return result

        redis = await get_redis()
        if not redis:
            checks.append(
                RedisVectorHealthService._check_item(
                    "redis_connect", False, "无法建立 Redis 连接"
                )
            )
            result["message"] = "无法连接 Redis，请检查 REDIS_HOST / REDIS_PORT / REDIS_PASSWORD"
            _CACHE = (now, result)
            return result

        try:
            await redis.ping()
            info = await redis.info("server")
            version = info.get("redis_version", "?")
            checks.append(
                RedisVectorHealthService._check_item(
                    "redis_connect", True, f"PING 成功，Redis {version}"
                )
            )
        except Exception as e:
            checks.append(
                RedisVectorHealthService._check_item("redis_connect", False, str(e))
            )
            result["message"] = f"Redis 连接失败: {e}"
            _CACHE = (now, result)
            return result

        has_search = False
        try:
            modules = await redis.execute_command("MODULE", "LIST")
            names: List[str] = []
            if modules:
                for m in modules:
                    if isinstance(m, dict):
                        names.append(str(m.get("name", "")).lower())
                    elif isinstance(m, (list, tuple)):
                        for i in range(len(m) - 1):
                            if m[i] == "name":
                                names.append(str(m[i + 1]).lower())
                                break
            has_search = any(n in ("search", "redisearch") for n in names)
            if has_search:
                checks.append(
                    RedisVectorHealthService._check_item(
                        "redisearch_module",
                        True,
                        "已加载 RediSearch 模块 (search)",
                    )
                )
            else:
                checks.append(
                    RedisVectorHealthService._check_item(
                        "redisearch_module",
                        False,
                        f"未检测到 search 模块，已加载: {', '.join(names) or '无'}",
                    )
                )
        except Exception as e:
            err = str(e).lower()
            if "unknown command" in err:
                checks.append(
                    RedisVectorHealthService._check_item(
                        "redisearch_module",
                        False,
                        "Redis 不支持 MODULE LIST / RediSearch，请改用 Redis Stack",
                    )
                )
            else:
                checks.append(
                    RedisVectorHealthService._check_item(
                        "redisearch_module", False, str(e)
                    )
                )

        db_ok = int(settings.REDIS_DB) == 0
        if db_ok:
            checks.append(
                RedisVectorHealthService._check_item(
                    "redis_db", True, "REDIS_DB=0，满足 RediSearch 要求"
                )
            )
        else:
            checks.append(
                RedisVectorHealthService._check_item(
                    "redis_db",
                    False,
                    f"当前 REDIS_DB={settings.REDIS_DB}，RediSearch 索引只能建在 db 0",
                )
            )

        vector_ok = False
        if has_search and db_ok:
            try:
                try:
                    await redis.execute_command("FT.DROPINDEX", _PROBE_INDEX)
                except Exception:
                    pass
                dim = 4
                await redis.execute_command(
                    "FT.CREATE",
                    _PROBE_INDEX,
                    "ON",
                    "HASH",
                    "PREFIX",
                    "1",
                    _PROBE_PREFIX,
                    "SCHEMA",
                    "embedding",
                    "VECTOR",
                    "HNSW",
                    "6",
                    "TYPE",
                    "FLOAT32",
                    "DIM",
                    str(dim),
                    "DISTANCE_METRIC",
                    "COSINE",
                )
                await redis.execute_command("FT.DROPINDEX", _PROBE_INDEX)
                vector_ok = True
                checks.append(
                    RedisVectorHealthService._check_item(
                        "vector_index",
                        True,
                        "FT.CREATE 向量索引 (HNSW COSINE) 测试通过",
                    )
                )
            except Exception as e:
                checks.append(
                    RedisVectorHealthService._check_item(
                        "vector_index", False, str(e)
                    )
                )
        elif has_search and not db_ok:
            checks.append(
                RedisVectorHealthService._check_item(
                    "vector_index",
                    False,
                    "因 REDIS_DB≠0 跳过向量索引创建测试",
                )
            )
        else:
            checks.append(
                RedisVectorHealthService._check_item(
                    "vector_index",
                    False,
                    "因缺少 RediSearch 模块跳过向量索引测试",
                )
            )

        result["ok"] = all(c["passed"] for c in checks)
        if result["ok"]:
            result["message"] = "Redis 向量检索环境正常，可使用记忆管理功能"
            result["hints"] = []
        else:
            failed = [c for c in checks if not c["passed"]]
            result["message"] = "；".join(c["message"] for c in failed[:3])
            if not has_search:
                result["message"] = (
                    "当前 Redis 不支持 RediSearch 向量索引。"
                    + (result["message"] or "")
                )
            elif not db_ok:
                result["message"] = (
                    f"请将 REDIS_DB 改为 0 后重启服务（当前为 {settings.REDIS_DB}）。"
                    + (result["message"] or "")
                )

        _CACHE = (now, result)
        return result

    @staticmethod
    def invalidate_cache() -> None:
        global _CACHE
        _CACHE = None
