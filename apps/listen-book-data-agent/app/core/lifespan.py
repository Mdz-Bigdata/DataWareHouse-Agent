from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.clients.redis_client_manager import redis_client_manager
from app.conf.app_config import app_config
from app.core.log import logger
from app.core.security import validate_secret_key
from app.models.mysql.base import Base
from app.services.auth_service import ensure_admin_seed
from app.services.llm_provider_service import ensure_llm_provider_seed


async def _apply_lightweight_migrations(connection: AsyncConnection) -> None:
    """create_all 不会给已有表加列，这里做幂等的补列迁移。"""
    result = await connection.execute(text("SHOW COLUMNS FROM query_trace LIKE 'user_id'"))
    if result.first() is None:
        logger.info("迁移：query_trace 增加 user_id 列")
        await connection.execute(
            text(
                "ALTER TABLE query_trace "
                "ADD COLUMN user_id VARCHAR(36) NULL, "
                "ADD INDEX ix_query_trace_user_id (user_id)"
            )
        )

    # Phase 1.4：users 表增加 data_scope 列（行级数据权限，JSON 字符串）
    result = await connection.execute(text("SHOW COLUMNS FROM users LIKE 'data_scope'"))
    if result.first() is None:
        logger.info("迁移：users 增加 data_scope 列（行级数据权限）")
        await connection.execute(text("ALTER TABLE users ADD COLUMN data_scope TEXT NULL"))

    result = await connection.execute(text("SHOW COLUMNS FROM query_trace_phase LIKE 'sql'"))
    if result.first() is None:
        logger.info("迁移：query_trace_phase 增加 SQL 尝试列")
        await connection.execute(text("ALTER TABLE query_trace_phase ADD COLUMN `sql` TEXT NULL"))

    result = await connection.execute(text("SHOW COLUMNS FROM query_trace LIKE 'policy_version'"))
    if result.first() is None:
        logger.info("迁移：query_trace 增加权限策略审计列")
        await connection.execute(
            text(
                "ALTER TABLE query_trace "
                "ADD COLUMN policy_version VARCHAR(64) NULL, "
                "ADD COLUMN policy_hash VARCHAR(64) NULL, "
                "ADD COLUMN policy_admin_bypass BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )

    result = await connection.execute(
        text("SHOW COLUMNS FROM query_trace LIKE 'query_set_id'")
    )
    if result.first() is None:
        logger.info("迁移：query_trace 增加语义发布版本审计列")
        await connection.execute(
            text(
                "ALTER TABLE query_trace "
                "ADD COLUMN semantic_release_id VARCHAR(36) NULL, "
                "ADD COLUMN semantic_release_version INT NULL, "
                "ADD COLUMN query_set_id VARCHAR(36) NULL, "
                "ADD COLUMN query_set_version INT NULL, "
                "ADD COLUMN business_rule_set_id VARCHAR(36) NULL, "
                "ADD COLUMN business_rule_set_version INT NULL"
            )
        )

    result = await connection.execute(
        text("SHOW COLUMNS FROM query_trace LIKE 'conversation_id'")
    )
    if result.first() is None:
        logger.info("迁移：query_trace 增加多轮会话与结果摘要列")
        await connection.execute(
            text(
                "ALTER TABLE query_trace "
                "ADD COLUMN conversation_id VARCHAR(36) NULL, "
                "ADD COLUMN parent_trace_id VARCHAR(36) NULL, "
                "ADD COLUMN regenerate_of_trace_id VARCHAR(36) NULL, "
                "ADD COLUMN standalone_question TEXT NULL, "
                "ADD COLUMN query_plan_summary JSON NULL, "
                "ADD COLUMN answer_summary TEXT NULL, "
                "ADD COLUMN chart_spec JSON NULL, "
                "ADD INDEX ix_query_trace_conversation_id (conversation_id), "
                "ADD INDEX ix_query_trace_parent_trace_id (parent_trace_id), "
                "ADD INDEX ix_query_trace_regenerate_of_trace_id (regenerate_of_trace_id)"
            )
        )

    # Phase 3.4：datasource 表由 Base.metadata.create_all 自动创建（无需手写迁移）
    # 若未来需要给 datasource 表补列，在此追加幂等迁移。


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("项目启动，执行各个客户端初始化")
    # Phase 4.3：最早期初始化 OpenTelemetry（在任何其他操作之前，确保 span 覆盖全）
    from app.core.telemetry import setup_telemetry

    setup_telemetry()
    # fail-fast：在任何外部连接之前先校验 JWT 签名密钥强度。
    validate_secret_key(app_config.auth.secret_key, app_config.app.environment)
    dw_mysql_client_manager.init_client()
    meta_mysql_client_manager.init_client()
    embedding_client_manager.init_client()
    qdrant_client_manager.init_client()
    es_client_manager.init()
    # Phase 4.1：Redis 初始化（失败不阻断，降级为直查）
    redis_client_manager.init_client()
    if meta_mysql_client_manager.client is None:
        raise RuntimeError("metadata database client is not initialized")
    async with meta_mysql_client_manager.client.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await _apply_lightweight_migrations(connection)
    async with meta_mysql_client_manager.session_factory() as session:
        await ensure_admin_seed(session)
        await ensure_llm_provider_seed(session)
    # Phase 2.1：幂等创建 Few-shot 自愈经验集合（独立于语义层版本，不随 rebuild 重建）
    if qdrant_client_manager.client is not None:
        from app.repositories.qdrant.feedback_qdrant_repository import (
            FeedbackQdrantRepository,
        )
        from app.repositories.qdrant.semantic_term_qdrant_repository import (
            SemanticTermQdrantRepository,
        )
        from app.repositories.qdrant.verified_query_qdrant_repository import (
            VerifiedQueryQdrantRepository,
        )

        await FeedbackQdrantRepository(qdrant_client_manager.client).ensure_collection()
        await SemanticTermQdrantRepository(qdrant_client_manager.client).ensure_collection()
        await VerifiedQueryQdrantRepository(qdrant_client_manager.client).ensure_collection()
    yield
    logger.info("项目关闭，执行各个客户端关闭")
    await dw_mysql_client_manager.close()
    await meta_mysql_client_manager.close()
    await es_client_manager.close()
    await qdrant_client_manager.close()
    await redis_client_manager.close()
