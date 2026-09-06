"""一键重建知识库索引：以 MySQL 当前活跃构建为源，重嵌字段/指标向量并同步枚举值。

与 build_meta_knowledge 脚本的区别：脚本以 DDL + YAML 为源并产生新 build；
本服务以网页维护后的 MySQL 元数据为源，只刷新检索索引（Qdrant 集合、ES 值索引），
通过 alias 原子切换，切换完成后下一次查询即生效。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.log import logger
from app.entities.value_info import ValueInfo
from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.meta_knowledge_service import MetaKnowledgeService

EMBEDDING_BATCH_SIZE = 20

# 单进程内存任务状态；同一时间只允许一个重建任务
_job: dict = {"status": "idle", "error": None, "started_at": None, "finished_at": None}
_lock = asyncio.Lock()


def rebuild_status() -> dict:
    return dict(_job)


async def start_rebuild(domain: str = "audio") -> bool:
    """启动后台重建。已有任务在跑时返回 False。"""
    if _lock.locked():
        return False
    asyncio.create_task(_run(domain))
    return True


async def _run(domain: str) -> None:
    async with _lock:
        _job.update(
            status="running", error=None,
            started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
        )
        try:
            summary = await rebuild_indexes(domain)
            _job.update(status="completed", finished_at=datetime.now().isoformat(timespec="seconds"))
            logger.info("知识库索引重建完成：{}", summary)
        except Exception as exc:
            _job.update(
                status="failed", error=str(exc)[:500],
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
            logger.exception("知识库索引重建失败")


async def rebuild_indexes(domain: str = "audio") -> dict:
    """核心重建流程：MySQL 活跃构建 → 新索引 → alias 切换。供任务与测试调用。"""
    if meta_mysql_client_manager.session_factory is None:
        raise RuntimeError("元数据库未初始化")
    if embedding_client_manager.client is None:
        raise RuntimeError("embedding 服务未初始化")

    rebuild_id = uuid.uuid4().hex[:12]
    async with meta_mysql_client_manager.session_factory() as session:
        repository = MetaMySQlRepository(session)
        columns = await repository.list_allowed_column_infos()
        metrics = await repository.list_metric_infos()
    if not columns and not metrics:
        raise RuntimeError("活跃构建中没有可索引的字段或指标")

    embedding_client = embedding_client_manager.client
    suffix = f"r{rebuild_id}"

    column_repository = ColumnQdrantRepository(qdrant_client_manager.client)
    metric_repository = MetricQdrantRepository(qdrant_client_manager.client)
    new_column_repo = ColumnQdrantRepository(
        qdrant_client_manager.client, f"{column_repository.alias_name}-{suffix}"
    )
    new_metric_repo = MetricQdrantRepository(
        qdrant_client_manager.client, f"{metric_repository.alias_name}-{suffix}"
    )
    value_alias_repo = ValueInfoRepository(es_client_manager.client)
    new_value_repo = ValueInfoRepository(
        es_client_manager.client, f"{value_alias_repo.alias_name}-{suffix}"
    )

    # 1.嵌入字段与指标
    await new_column_repo.ensure_collection()
    await new_metric_repo.ensure_collection()
    await _save_vectors(
        columns, rebuild_id=rebuild_id, kind="column",
        text_factory=MetaKnowledgeService._column_embedding_text,
        embedding_client=embedding_client, upsert=new_column_repo.upsert,
    )
    if metrics:
        await _save_vectors(
            metrics, rebuild_id=rebuild_id, kind="metric",
            text_factory=MetaKnowledgeService._metric_embedding_text,
            embedding_client=embedding_client, upsert=new_metric_repo.upsert,
        )

    # 2.枚举值索引：用 MySQL 中已存的示例/枚举值，不再扫业务库
    value_infos = _collect_value_infos(columns, rebuild_id)
    await new_value_repo.ensure_index()
    if value_infos:
        await new_value_repo.upsert(value_infos)

    # 3.原子切换 alias；任何一步失败都不影响旧索引
    await new_column_repo.set_alias()
    await new_metric_repo.set_alias()
    await new_value_repo.set_alias()

    return {
        "rebuild_id": rebuild_id,
        "columns": len(columns),
        "metrics": len(metrics),
        "values": len(value_infos),
        "column_collection": new_column_repo.coll_name,
        "metric_collection": new_metric_repo.coll_name,
        "value_index": new_value_repo.index_name,
    }


def _collect_value_infos(columns, rebuild_id: str) -> list[ValueInfo]:
    value_infos: list[ValueInfo] = []
    for column in columns:
        if not column.sync or column.sensitive:
            continue
        values = list(dict.fromkeys([*column.enum_values, *column.examples]))[:1000]
        for value in values:
            text = str(value)
            if not text:
                continue
            value_infos.append(
                ValueInfo(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{rebuild_id}:value:{column.id}:{text}")),
                    value=text,
                    column_id=column.id,
                    build_id=rebuild_id,
                )
            )
    return value_infos


async def _save_vectors(
    items: list,
    *,
    rebuild_id: str,
    kind: str,
    text_factory,
    embedding_client,
    upsert,
) -> None:
    texts = [text_factory(item) for item in items]
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        embeddings.extend(await embedding_client.aembed_documents(texts[start : start + EMBEDDING_BATCH_SIZE]))
        if start:
            await asyncio.sleep(0)  # 长批次让出事件循环
    ids = [
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{rebuild_id}:{kind}:{getattr(item, 'id')}"))
        for item in items
    ]
    await upsert(ids, items, embeddings, batch_size=64)
