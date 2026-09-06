"""召回测试：复现查询管线的关键词抽取与向量召回，不触发 LLM 生成。"""

from __future__ import annotations

import jieba.analyse

from app.agent.retrieval import (
    allowed_columns,
    batched_vector_search,
    lexical_rank,
    recall_terms,
)
from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository

_ALLOW_POS = (
    "n", "nr", "ns", "nt", "nz", "v", "vn", "a", "an", "eng", "i", "l",
)


def extract_terms(question: str) -> list[str]:
    """与 extract_keywords 节点一致的分词逻辑（无 analysis_plan 场景）。"""
    keywords = jieba.analyse.extract_tags(question, topK=10, allowPOS=_ALLOW_POS)
    keywords.append(question)
    return list(dict.fromkeys(keyword for keyword in keywords if keyword))


async def recall_test(
    question: str,
    *,
    embedding_client,
    column_qdrant_repository,
    metric_qdrant_repository,
    meta_mysql_repository: MetaMySQlRepository,
) -> dict:
    """返回本次问题召回的表、字段、指标；向量服务不可用时回退词法检索。"""
    keywords = extract_terms(question)
    terms = recall_terms(keywords, None)
    warnings: list[str] = []

    try:
        columns = await batched_vector_search(
            terms=terms,
            embedding_client=embedding_client,
            search=column_qdrant_repository.search,
        )
        columns = allowed_columns(columns)[:24]
    except Exception:
        candidates = await meta_mysql_repository.list_allowed_column_infos()
        columns = lexical_rank(
            candidates,
            terms,
            lambda item: " ".join([item.name, item.description, *item.alias]),
        )[:24]
        warnings.append("字段向量召回不可用，已切换为元数据词法检索。")

    try:
        metrics = (
            await batched_vector_search(
                terms=terms,
                embedding_client=embedding_client,
                search=metric_qdrant_repository.search,
            )
        )[:12]
    except Exception:
        candidates = await meta_mysql_repository.list_metric_infos()
        metrics = lexical_rank(
            candidates,
            terms,
            lambda item: " ".join([item.name, item.description, item.formula, *item.alias]),
        )[:12]
        warnings.append("指标向量召回不可用，已切换为元数据词法检索。")

    tables = list(dict.fromkeys(column.table_id for column in columns))
    return {
        "question": question,
        "keywords": keywords,
        "terms": terms,
        "tables": tables,
        "columns": [_column_payload(column) for column in columns],
        "metrics": [_metric_payload(metric) for metric in metrics],
        "warnings": warnings,
    }


def _column_payload(column: ColumnInfo) -> dict:
    return {
        "id": column.id,
        "table_id": column.table_id,
        "name": column.name,
        "description": column.description,
        "alias": column.alias,
        "role": column.role,
    }


def _metric_payload(metric: MetricInfo) -> dict:
    return {
        "id": metric.id,
        "name": metric.name,
        "description": metric.description,
        "alias": metric.alias,
        "formula": metric.formula,
    }
