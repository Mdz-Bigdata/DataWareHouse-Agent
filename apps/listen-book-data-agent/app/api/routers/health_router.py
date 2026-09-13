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
from app.conf.app_config import app_config
from app.services.health_service import (
    Dependency,
    DependencyUnavailable,
    readiness_report,
    tri_state,
)

health_router = APIRouter(tags=["服务健康"])


@health_router.get("/health")
async def health() -> dict:
    """Liveness: the HTTP process is accepting requests."""

    return {"status": "ok"}


@health_router.get("/ready")
async def ready():
    """Readiness: 查询链路需要的依赖是否就位。

    三态口径（详见 app/services/health_service.py）：
    ``ok`` / ``not_configured``（按设计未启用，不拉低整体状态）/ ``error``（真故障）。

    * 元数据库、业务库、Qdrant、ES 是必需依赖：任何失败都是 ``error``。
    * embedding 是可选依赖：它有独立的 compose profile，``full`` 里根本不启动。
      默认 auto 档下「主机名解析不出来」判为 ``not_configured``；一旦用
      ``EMBEDDING_ENABLED=true`` 显式启用，任何失败都必须报 ``error``。
    * Redis 是加速层：失败只报 ``degraded``，永远不影响整体 ready（降级直查）。
    """

    report = await readiness_report(
        [
            Dependency(
                "metadata_mysql",
                lambda: _probe_mysql(meta_mysql_client_manager),
                host=app_config.db_meta.host,
                port=app_config.db_meta.port,
            ),
            Dependency(
                "warehouse_mysql",
                lambda: _probe_mysql(dw_mysql_client_manager),
                host=app_config.db_dw.host,
                port=app_config.db_dw.port,
            ),
            Dependency(
                "qdrant",
                _probe_qdrant,
                host=app_config.qdrant.host,
                port=app_config.qdrant.port,
            ),
            Dependency(
                "elasticsearch",
                _probe_elasticsearch,
                host=app_config.es.host,
                port=app_config.es.port,
            ),
            Dependency(
                "embedding",
                _probe_embedding,
                optional=True,
                enabled=tri_state(getattr(app_config.embedding, "enabled", "auto")),
                host=app_config.embedding.host,
                port=app_config.embedding.port,
            ),
            Dependency(
                "redis",
                _probe_redis,
                optional=True,
                soft_fail=True,
                host=app_config.redis.host,
                port=app_config.redis.port,
            ),
        ]
    )
    return JSONResponse(status_code=200 if report["status"] == "ready" else 503, content=report)


async def _probe_redis() -> None:
    if not redis_client_manager.available:
        raise DependencyUnavailable("client_not_initialized")
    await redis_client_manager.client.ping()


async def _probe_mysql(manager) -> None:
    if manager.session_factory is None:
        raise DependencyUnavailable("client_not_initialized")
    async with manager.session_factory() as session:
        await session.execute(text("SELECT 1"))


async def _probe_qdrant() -> None:
    if qdrant_client_manager.client is None:
        raise DependencyUnavailable("client_not_initialized")
    await qdrant_client_manager.client.get_collections()


async def _probe_elasticsearch() -> None:
    if es_client_manager.client is None or not await es_client_manager.client.ping():
        raise DependencyUnavailable("client_not_ready")


async def _probe_embedding() -> None:
    if embedding_client_manager.client is None:
        raise DependencyUnavailable("client_not_initialized")
    await embedding_client_manager.client.aembed_query("健康检查")
