import datetime
import logging
from typing import Dict, Any, List

from sqlalchemy import text
from app.core.orm import AsyncSessionLocal
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

DATASET_NAMES_REDIS_KEY = "kb:citation:dataset_names"
DOC_NAMES_REDIS_KEY = "kb:citation:doc_names"


class KnowledgeMetricsService:
    """
    Service to process and persist RAG citation and search metrics.
    """

    @staticmethod
    def _fill_trend_gaps(trend: list, start_date: str, end_date: str) -> list:
        """补全日期范围内无数据的日期，确保前端折线图能连续渲染。"""
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        by_date = {item["date"]: item for item in trend}
        filled = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            filled.append(by_date.get(date_str, {
                "date": date_str,
                "search_count": 0,
                "citation_count": 0,
            }))
            current += datetime.timedelta(days=1)
        return filled

    @staticmethod
    def _decode_redis_hash(raw_map) -> Dict[str, str]:
        if not raw_map:
            return {}
        return {
            (k.decode("utf-8") if isinstance(k, bytes) else str(k)): (
                v.decode("utf-8") if isinstance(v, bytes) else str(v)
            )
            for k, v in raw_map.items()
        }

    @staticmethod
    def _fallback_dataset_name(dataset_id: str) -> str:
        return f"知识库: {dataset_id[:8]}"

    @classmethod
    async def resolve_dataset_names(cls, dataset_ids: List[str]) -> Dict[str, str]:
        """解析知识库中文名：Redis 缓存 → 本地元数据表 → RAGFlow 列表。"""
        ids = list({
            str(ds_id).strip()
            for ds_id in dataset_ids
            if ds_id and str(ds_id).strip() not in {"", "default"}
        })
        if not ids:
            return {}

        resolved: Dict[str, str] = {}
        redis = await get_redis()
        if redis:
            cached = cls._decode_redis_hash(await redis.hgetall(DATASET_NAMES_REDIS_KEY))
            for ds_id in ids:
                name = (cached.get(ds_id) or "").strip()
                if name:
                    resolved[ds_id] = name

        missing = [ds_id for ds_id in ids if ds_id not in resolved]
        if missing:
            async with AsyncSessionLocal() as session:
                placeholders = ", ".join(f":id{i}" for i in range(len(missing)))
                params = {f"id{i}": ds_id for i, ds_id in enumerate(missing)}
                sql = (
                    "SELECT ragflow_dataset_id, name FROM knowledge_base_metadata "
                    f"WHERE ragflow_dataset_id IN ({placeholders})"
                )
                res = await session.execute(text(sql), params)
                for row in res.fetchall():
                    name = (row[1] or "").strip()
                    if name:
                        resolved[row[0]] = name

        missing = [ds_id for ds_id in ids if ds_id not in resolved]
        if missing:
            try:
                from app.services.ai.ragflow_client import RagFlowClient

                client = RagFlowClient(config_prefix="knowledge_ragflow")
                ragflow_datasets = await client.list_datasets(page_size=500)
                missing_set = set(missing)
                for ds in ragflow_datasets:
                    ds_id = str(ds.get("id") or "").strip()
                    name = str(ds.get("name") or "").strip()
                    if ds_id in missing_set and name:
                        resolved[ds_id] = name
            except Exception as e:
                logger.warning(f"[KnowledgeMetricsService] RAGFlow dataset name lookup failed: {e}")

        if redis:
            for ds_id, name in resolved.items():
                await redis.hset(DATASET_NAMES_REDIS_KEY, ds_id, name)

        return resolved

    @classmethod
    async def sync_redis_metrics_to_db(cls):
        """
        Sync aggregated search and citation statistics from Redis to DB.
        """
        redis = await get_redis()
        if not redis:
            logger.error("[KnowledgeMetricsService] Redis client unavailable; sync skipped.")
            return

        # Scan for keys matching pattern kb:citation:stats:YYYY-MM-DD:*
        keys = await redis.keys("kb:citation:stats:*")
        if not keys:
            logger.info("[KnowledgeMetricsService] No redis metrics keys found for sync.")
            return

        metrics_map = {}
        
        # Get cached doc/dataset name maps
        doc_names = cls._decode_redis_hash(await redis.hgetall(DOC_NAMES_REDIS_KEY))
        dataset_names = cls._decode_redis_hash(await redis.hgetall(DATASET_NAMES_REDIS_KEY))

        for key_bytes in keys:
            key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else key_bytes
            parts = key.split(":")
            # Structure: ["kb", "citation", "stats", "YYYY-MM-DD", "target_type", "action"]
            if len(parts) < 6:
                continue
                
            date_str = parts[3]
            target_type = parts[4]  # dataset or document
            action = parts[5]       # search or citation
            
            hash_data = await redis.hgetall(key)
            if not hash_data:
                continue
                
            if date_str not in metrics_map:
                metrics_map[date_str] = {}
            if target_type not in metrics_map[date_str]:
                metrics_map[date_str][target_type] = {}
                
            for t_id_bytes, val_bytes in hash_data.items():
                t_id = t_id_bytes.decode("utf-8") if isinstance(t_id_bytes, bytes) else t_id_bytes
                val = int(val_bytes) if val_bytes else 0
                
                if t_id not in metrics_map[date_str][target_type]:
                    metrics_map[date_str][target_type][t_id] = {"search": 0, "citation": 0}
                    
                metrics_map[date_str][target_type][t_id][action] = val

        dataset_ids_to_resolve = sorted({
            target_id
            for type_map in metrics_map.values()
            for target_type, id_map in type_map.items()
            if target_type == "dataset"
            for target_id in id_map
        })
        if dataset_ids_to_resolve:
            resolved_names = await cls.resolve_dataset_names(dataset_ids_to_resolve)
            dataset_names.update(resolved_names)

        # 本地元数据表优先级最高
        async with AsyncSessionLocal() as session:
            try:
                res = await session.execute(text("SELECT ragflow_dataset_id, name FROM knowledge_base_metadata"))
                rows = res.fetchall()
                for r in rows:
                    if r[1]:
                        dataset_names[r[0]] = r[1]
            except Exception as e:
                logger.warning(f"[KnowledgeMetricsService] Failed to prefetch dataset names: {e}")

        # Batch insert or update metrics in DB
        async with AsyncSessionLocal() as session:
            try:
                for date_str, type_map in metrics_map.items():
                    try:
                        metric_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                        
                    for target_type, id_map in type_map.items():
                        for target_id, counts in id_map.items():
                            search_count = counts.get("search", 0)
                            citation_count = counts.get("citation", 0)
                            
                            target_name = None
                            if target_type == "dataset":
                                target_name = dataset_names.get(target_id) or cls._fallback_dataset_name(target_id)
                            else:
                                target_name = doc_names.get(target_id) or f"文档: {target_id[:8]}"

                            # MySQL INSERT ... ON DUPLICATE KEY UPDATE for clean upsert
                            sql = """
                                INSERT INTO knowledge_base_metrics 
                                (metric_date, target_type, target_id, target_name, search_count, citation_count, created_at, updated_at)
                                VALUES 
                                (:metric_date, :target_type, :target_id, :target_name, :search_count, :citation_count, NOW(), NOW())
                                ON DUPLICATE KEY UPDATE 
                                search_count = search_count + :search_count,
                                citation_count = citation_count + :citation_count,
                                target_name = VALUES(target_name),
                                updated_at = NOW()
                            """
                            await session.execute(text(sql), {
                                "metric_date": metric_date,
                                "target_type": target_type,
                                "target_id": target_id,
                                "target_name": target_name,
                                "search_count": search_count,
                                "citation_count": citation_count
                            })
                
                await session.commit()
                logger.info("[KnowledgeMetricsService] Sync metrics to DB successful.")
                
                # Delete successfully processed keys from Redis
                for key_bytes in keys:
                    await redis.delete(key_bytes)
                
            except Exception as db_err:
                await session.rollback()
                logger.error(f"[KnowledgeMetricsService] DB metrics sync failed: {db_err}", exc_info=True)

    @classmethod
    async def get_metrics_summary(cls, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Get aggregated operational metrics summary for frontend charts dashboard.
        """
        async with AsyncSessionLocal() as session:
            # 1. Dataset citations Top-10
            dataset_sql = """
                SELECT target_id, SUM(search_count) as total_search, SUM(citation_count) as total_citation
                FROM knowledge_base_metrics
                WHERE target_type = 'dataset' AND metric_date BETWEEN :start_date AND :end_date
                GROUP BY target_id
                ORDER BY total_citation DESC, total_search DESC
                LIMIT 10
            """
            
            # 2. Document citations Top-10
            document_sql = """
                SELECT target_id, target_name, SUM(search_count) as total_search, SUM(citation_count) as total_citation
                FROM knowledge_base_metrics
                WHERE target_type = 'document' AND metric_date BETWEEN :start_date AND :end_date
                GROUP BY target_id, target_name
                ORDER BY total_citation DESC, total_search DESC
                LIMIT 10
            """
            
            # 3. 按日趋势（仅统计 document 维度，避免与 dataset 重复计数）
            trend_sql = """
                SELECT metric_date, SUM(search_count) as total_search, SUM(citation_count) as total_citation
                FROM knowledge_base_metrics
                WHERE target_type = 'document' AND metric_date BETWEEN :start_date AND :end_date
                GROUP BY metric_date
                ORDER BY metric_date ASC
            """

            # 4. 活跃文献源总数（不限 Top 10）
            active_docs_sql = """
                SELECT COUNT(DISTINCT target_id)
                FROM knowledge_base_metrics
                WHERE target_type = 'document'
                  AND metric_date BETWEEN :start_date AND :end_date
                  AND search_count > 0
            """
            
            try:
                res_ds = await session.execute(text(dataset_sql), {"start_date": start_date, "end_date": end_date})
                dataset_rows = res_ds.fetchall()
                dataset_name_map = await cls.resolve_dataset_names([r[0] for r in dataset_rows])
                datasets = [{
                    "id": r[0],
                    "name": dataset_name_map.get(r[0]) or cls._fallback_dataset_name(r[0]),
                    "search_count": int(r[1]),
                    "citation_count": int(r[2]),
                } for r in dataset_rows]
                
                res_doc = await session.execute(text(document_sql), {"start_date": start_date, "end_date": end_date})
                documents = [{
                    "id": r[0], "name": r[1], "search_count": int(r[2]), "citation_count": int(r[3])
                } for r in res_doc.fetchall()]
                
                res_trend = await session.execute(text(trend_sql), {"start_date": start_date, "end_date": end_date})
                trend = [{
                    "date": r[0].strftime("%Y-%m-%d") if isinstance(r[0], datetime.date) else str(r[0]),
                    "search_count": int(r[1]),
                    "citation_count": int(r[2])
                } for r in res_trend.fetchall()]
                trend = cls._fill_trend_gaps(trend, start_date, end_date)

                res_active = await session.execute(text(active_docs_sql), {"start_date": start_date, "end_date": end_date})
                active_docs = int(res_active.scalar() or 0)
                
                return {
                    "datasets": datasets,
                    "documents": documents,
                    "trend": trend,
                    "active_docs": active_docs,
                }
            except Exception as e:
                logger.error(f"[KnowledgeMetricsService] Query metrics summary failed: {e}", exc_info=True)
                return {"datasets": [], "documents": [], "trend": [], "active_docs": 0}
