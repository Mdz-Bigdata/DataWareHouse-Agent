# 定义异步函数，返回业务所需要的对象
from typing import Annotated

from async_lru import alru_cache
from fastapi.params import Depends
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import dw_mysql_client_manager, meta_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.log import logger
from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.business_rule_repository import BusinessRuleRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.repositories.mysql.semantic_term_repository import SemanticTermRepository
from app.repositories.mysql.verified_query_repository import QuerySetRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.feedback_qdrant_repository import FeedbackQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.repositories.qdrant.semantic_term_qdrant_repository import (
    SemanticTermQdrantRepository,
)
from app.repositories.qdrant.verified_query_qdrant_repository import (
    VerifiedQueryQdrantRepository,
)
from app.services.business_rule_service import BusinessRuleService
from app.services.feedback_learning_service import FeedbackLearningService
from app.services.query_service import QueryService
from app.services.query_set_match_service import QuerySetMatchService
from app.services.semantic_term_service import SemanticTermService


async def get_embedding_client() -> HuggingFaceEndpointEmbeddings:
    return embedding_client_manager.client


async def get_qdrant_client() -> AsyncQdrantClient:
    return qdrant_client_manager.client


async def get_column_qdrant_repository(
    client: Annotated[AsyncQdrantClient, Depends(get_qdrant_client)],
) -> ColumnQdrantRepository:
    return ColumnQdrantRepository(client)


async def get_metric_qdrant_repository(
    client: Annotated[AsyncQdrantClient, Depends(get_qdrant_client)],
) -> MetricQdrantRepository:
    return MetricQdrantRepository(client)


@alru_cache
async def get_feedback_learning_service() -> FeedbackLearningService:
    """Phase 2.1：Few-shot 自愈学习服务（单例缓存）。

    feedback 集合独立于语义层版本，不随 knowledge rebuild 重建，
    因此这里直接用共享的 qdrant client，不涉及 build_id/alias。
    """

    logger.debug("创建 feedback_learning_service 对象")
    feedback_repository = FeedbackQdrantRepository(qdrant_client_manager.client)
    return FeedbackLearningService(
        feedback_repository=feedback_repository,
        embedding_client=embedding_client_manager.client,
    )


@alru_cache  # 第一次会创建对象，缓存后，下次调用会直接返回缓存对象
async def get_value_es_repository() -> ValueInfoRepository:
    logger.debug("创建 value_es_repository 对象")
    return ValueInfoRepository(es_client_manager.client)


async def get_dw_session():
    """通过dwmysqlcliengt中session_factory获取 每次必须获取新Session对象"""
    async with dw_mysql_client_manager.session_factory() as dw_session:
        try:
            yield dw_session  # 持久层操作数据库，操作完后Session使用完毕
            # await dw_session.commit()
        except Exception:
            # await dw_session.rollback()
            raise


async def get_dw_mysql_repository(
    dw_session: Annotated[AsyncSession, Depends(get_dw_session)],
) -> DWMySQlRepository:
    return DWMySQlRepository(dw_session)


async def get_meta_session():
    async with meta_mysql_client_manager.session_factory() as meta_session:
        try:
            yield meta_session  # 持久层操作数据库，操作完后Session使用完毕
            # await meta_session.commit()
        except Exception:
            # await meta_session.rollback()
            raise


async def get_meta_mysql_repository(
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
) -> MetaMySQlRepository:
    return MetaMySQlRepository(meta_session)


async def get_query_trace_session():
    """查询追踪使用独立 Session，避免与并行图节点共用同一连接。"""

    async with meta_mysql_client_manager.session_factory() as meta_session:
        try:
            yield meta_session
        except Exception:
            raise


async def get_query_trace_repository(
    meta_session: Annotated[AsyncSession, Depends(get_query_trace_session)],
) -> QueryTraceRepository:
    return QueryTraceRepository(meta_session)


async def get_query_service(
    embedding_client: Annotated[HuggingFaceEndpointEmbeddings, Depends(get_embedding_client)],
    column_qdrant_repository: Annotated[
        ColumnQdrantRepository, Depends(get_column_qdrant_repository)
    ],
    metric_qdrant_repository: Annotated[
        MetricQdrantRepository, Depends(get_metric_qdrant_repository)
    ],
    value_es_repository: Annotated[ValueInfoRepository, Depends(get_value_es_repository)],
    dw_mysql_repository: Annotated[DWMySQlRepository, Depends(get_dw_mysql_repository)],
    meta_mysql_repository: Annotated[MetaMySQlRepository, Depends(get_meta_mysql_repository)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
    feedback_learning_service: Annotated[
        FeedbackLearningService, Depends(get_feedback_learning_service)
    ],
) -> QueryService:
    return QueryService(
        embedding_client=embedding_client,
        column_qdrant_repository=column_qdrant_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        value_es_repository=value_es_repository,
        dw_mysql_repository=dw_mysql_repository,
        meta_mysql_repository=meta_mysql_repository,
        query_trace_repository=query_trace_repository,
        feedback_learning_service=feedback_learning_service,
        query_set_match_service=QuerySetMatchService(
            QuerySetRepository(meta_mysql_repository.session),
            VerifiedQueryQdrantRepository(qdrant_client_manager.client),
            embedding_client,
        ),
        business_rule_service=BusinessRuleService(
            BusinessRuleRepository(meta_mysql_repository.session)
        ),
        semantic_term_service=SemanticTermService(
            SemanticTermRepository(meta_mysql_repository.session),
            SemanticTermQdrantRepository(qdrant_client_manager.client),
            embedding_client,
        ),
    )
