import asyncio
from typing import NotRequired, TypedDict

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.business_rule_service import BusinessRuleService
from app.services.feedback_learning_service import FeedbackLearningService
from app.services.query_set_match_service import QuerySetMatchService


class DataAgentContext(TypedDict):
    """TypedDict就是字典,runtime中运行上下文结构定义，包含各个节点运行需要静态依赖对象（只读）"""

    dw_mysql_repository: DWMySQlRepository
    meta_mysql_repository: MetaMySQlRepository
    # 字段与指标召回会并行运行，但 AsyncSession 不允许并发操作。
    # 查询级锁只串行化很短的元数据读取，向量召回仍保持并行。
    meta_repository_lock: asyncio.Lock
    embedding_client: HuggingFaceEndpointEmbeddings
    column_qdrant_repository: ColumnQdrantRepository
    metric_qdrant_repository: MetricQdrantRepository
    value_es_repository: ValueInfoRepository
    # Phase 2.1：Few-shot 自愈学习服务（可选注入，缺失时 correct_sql 回退零样本）
    feedback_learning_service: FeedbackLearningService | None
    # 可信案例治理服务可选注入，图级测试和离线调用缺失时保持旧链路。
    query_set_match_service: NotRequired[QuerySetMatchService | None]
    business_rule_service: NotRequired[BusinessRuleService | None]
