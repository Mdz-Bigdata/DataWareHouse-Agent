from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from starlette.responses import JSONResponse

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.clients.redis_client_manager import redis_client_manager
from app.services.health_service import readiness_report

health_router = APIRouter(tags=["服务健康"])


@health_router.get("/health")
async def health() -> dict:
    """Liveness: the HTTP process is accepting requests."""

    return {"status": "ok"}


@health_router.get("/ready")
async def ready():
    """Readiness: all dependencies needed for a query are reachable.

    Redis 标记为可选（available=False 不影响 ready 状态，仅降级直查）。
    """

    report = await readiness_report(
        {
            "metadata_mysql": lambda: _probe_mysql(meta_mysql_client_manager),
            "warehouse_mysql": lambda: _probe_mysql(dw_mysql_client_manager),
            "qdrant": _probe_qdrant,
            "elasticsearch": _probe_elasticsearch,
            "embedding": _probe_embedding,
        }
    )
    # Phase 4.1：Redis 是可选依赖，单独探测但不影响整体 ready 状态
    redis_status = await _probe_redis_optional()
    report["dependencies"]["redis"] = redis_status
    return JSONResponse(status_code=200 if report["status"] == "ready" else 503, content=report)


async def _probe_redis_optional() -> dict:
    """Redis 探针：不可用时返回 degraded 但不影响整体 ready。"""

    if not redis_client_manager.available:
        return {"status": "degraded", "detail": "not_configured_or_unavailable"}
    try:
        await redis_client_manager.client.ping()
        return {"status": "ok"}
    except Exception:
        return {"status": "degraded", "detail": "ping_failed"}


async def _probe_mysql(manager) -> None:
    if manager.session_factory is None:
        raise RuntimeError("client_not_initialized")
    async with manager.session_factory() as session:
        await session.execute(text("SELECT 1"))


async def _probe_qdrant() -> None:
    if qdrant_client_manager.client is None:
        raise RuntimeError("client_not_initialized")
    await qdrant_client_manager.client.get_collections()


async def _probe_elasticsearch() -> None:
    if es_client_manager.client is None or not await es_client_manager.client.ping():
        raise RuntimeError("client_not_ready")


async def _probe_embedding() -> None:
    if embedding_client_manager.client is None:
        raise RuntimeError("client_not_initialized")
    await embedding_client_manager.client.aembed_query("健康检查")
