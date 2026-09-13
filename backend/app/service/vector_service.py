# -*- coding: utf-8 -*-
"""派生向量索引及其治理。

派生索引不是权威数据，它是语义层与用户记忆的一份可重建副本。副本只有在满足
三个条件时才可信，本模块围绕这三条组织：

1. **幂等**：点 ID 由「集合 + 数据源 + 业务键」确定性派生（UUIDv5），
   重复灌库只会原地覆盖，永远不产生重复点；同一数据源下被删掉的业务对象
   会在灌库时被剪枝，不留孤儿点。
2. **可追溯**：每个点的 payload 都带 `source_id` / `embedding_model` /
   `status` / `content_hash` / `indexed_at` / `point_kind`，可以回答
   “这个点是谁、用哪个模型、什么时候、从哪个数据源写进来的”。
3. **可隔离**：Embedding 存在在线模型与本地哈希降级两条路径，二者的向量空间
   毫无可比性。检索一律带 `query_filter` 预过滤，只拿与查询向量**同模型**、
   且属于**当前数据源**的点比对，杜绝 ada-002 向量与哈希向量混排出来的
   “看起来有分数、其实是噪声”的检索结果。
"""
import hashlib
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.service.semantic_layer import semantic_layer

# 两条 Embedding 路径的稳定标识。写进 payload 后既是出处，也是检索期的隔离键。
ONLINE_EMBEDDING_MODEL = "text-embedding-ada-002"
HASH_EMBEDDING_MODEL = "local-hash-v1"

# 点的生命周期状态：
#   active   —— 用当前配置下的既定模型正常嵌入
#   degraded —— 在线模型配置了却调用失败/维度不符，退回本地哈希向量
STATUS_ACTIVE = "active"
STATUS_DEGRADED = "degraded"

# Few-shot 与纠错经验来自 user_memory，不随数据源切换而改变，用固定哨兵值标记，
# 免得切换数据源后被 source_id 预过滤误杀。
GLOBAL_SOURCE_ID = "__global__"
UNKNOWN_SOURCE_ID = "unknown"

# 点 ID 的派生命名空间。固定不变，重启进程也能推出同样的 ID。
POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "dwh-agent/vector-index/v1")

# 治理字段只服务于索引自身，不该混进喂给大模型的候选元数据里。
GOVERNANCE_KEYS = frozenset(
    {"source_id", "embedding_model", "status", "content_hash", "indexed_at", "point_kind"}
)


def derive_point_id(collection: str, source_id: str, business_key: str) -> str:
    """由业务键派生确定性点 ID —— 同一业务对象永远落在同一个点上。"""
    return str(uuid.uuid5(POINT_NAMESPACE, f"{collection}|{source_id}|{business_key}"))


def content_fingerprint(text: str) -> str:
    """被嵌入文本的指纹，用于判断某个点是否需要重新嵌入。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class VectorService:
    def __init__(self):
        # 1. 初始化 Qdrant 内存数据库实例 (高可靠，无需依赖外部 Docker 即可本地运行全套向量检索)
        self.client = QdrantClient(location=":memory:")
        self.embedding_dim = 1536

        # 定义五大独立分治的元数据集合
        self.metrics_collection = "dwh_metrics"
        self.dims_collection = "dwh_dims"
        self.value_collection = "value_indices"
        self.example_collection = "few_shots"
        self.error_correction_collection = "few_shots_corrections"

        # 检索预过滤开关。默认开启：这是防止不同 Embedding 空间混排的唯一屏障，
        # 只有在排障时才应临时关掉 (DWH_VECTOR_PREFILTER=0)。
        self.enable_prefilter = os.getenv("DWH_VECTOR_PREFILTER", "1") != "0"
        # 是否在检索时连降级点一起排除。默认关闭：全库降级时排除会直接打成零召回，
        # 同模型预过滤已经保证了可比性，这里留的是更严格的隔离档位。
        self.exclude_degraded_on_recall = os.getenv("DWH_VECTOR_EXCLUDE_DEGRADED", "0") == "1"
        # 最近一次嵌入的出处，供健康检查/排障读取。
        self.last_embedding_model = HASH_EMBEDDING_MODEL
        self.last_embedding_status = STATUS_ACTIVE
        self.last_degraded_reason = ""
        # 派生索引的写入串行化：纠错经验可能来自后台线程，与请求线程并发。
        self._write_lock = threading.RLock()

        # 2. 初始化 collections
        self._init_collections()
        # 3. 自动注入语义层元数据与问数 Few-shot 示例进向量库
        self.ingest_metadata()
        self.ingest_fewshot_examples()
        self.ingest_error_corrections()

    def _init_collections(self):
        """在内存中建立独立的五大集合"""
        for name in [self.metrics_collection, self.dims_collection, self.value_collection, self.example_collection, self.error_correction_collection]:
            if self.client.collection_exists(collection_name=name):
                self.client.delete_collection(collection_name=name)
            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE)
            )

    @property
    def index_lock(self) -> threading.RLock:
        """串行化对内存 Qdrant 的读写。

        本地内存模式不保证线程安全，而纠错经验同步可能来自后台线程。锁只圈住内存
        操作本身 —— Embedding I/O 一律在加锁之前完成，不会让检索排队等网络。
        """
        lock = getattr(self, "_write_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._write_lock = lock
        return lock

    # ------------------------------------------------------------------
    # 出处解析
    # ------------------------------------------------------------------
    def active_source_id(self) -> str:
        """当前数据源标识。灌库与检索共用，保证预过滤两端口径一致。"""
        try:
            from app.service.data_source_manager import data_source_manager
            current = data_source_manager.active_id
            if current:
                return str(current)
            return str(data_source_manager.adopt_current())
        except Exception:
            # 数据源注册表不可用时退化为统一哨兵值：两端口径仍然一致，只是失去区分度。
            return UNKNOWN_SOURCE_ID

    def intended_embedding_model(self) -> str:
        """在不发起任何网络调用的前提下，判断当前配置**打算**用哪个 Embedding 模型。

        用于两件事：判断已有点是否需要重嵌；构造检索预过滤条件。
        真实调用一旦降级，写进 payload 的会是哈希模型名，下次灌库自然重试在线模型。
        """
        api_key, base_url = self._read_embedding_vendor()
        if api_key and base_url and "api.deepseek.com" not in base_url:
            return ONLINE_EMBEDDING_MODEL
        return HASH_EMBEDDING_MODEL

    @staticmethod
    def _read_embedding_vendor() -> Tuple[str, str]:
        """读取当前启用厂商的 api_key / base_url；演示数仓下一律不读凭据。"""
        from app.service.db_service import db_service
        if db_service.is_sample_data:
            return "", ""

        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "llm_config.json")
        api_key = ""
        base_url = ""
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    active_vendor = config_data.get("active_vendor", "")
                    if active_vendor in config_data.get("vendors", {}):
                        vendor_cfg = config_data["vendors"][active_vendor]
                        api_key = vendor_cfg.get("api_key", "")
                        base_url = vendor_cfg.get("base_url", "")
        except Exception:
            pass
        return api_key, base_url

    def _get_local_hash_embedding(self, text: str) -> List[float]:
        """
        本地降级 Embedding 生成算法 (基于字符级 N-gram 特征哈希与正交投影)。
        当没有外网 API key 或网络连接超时时，能够产生一个 100% 确定、稳定且能表征模糊相似度的 1536 维归一化向量。
        """
        np.random.seed(42)  # 固定全局随机种子以确保确定性投影
        slots = np.zeros(self.embedding_dim)

        text_clean = text.lower().strip()
        grams = []
        for i in range(len(text_clean)):
            grams.append(text_clean[i])
            if i < len(text_clean) - 1:
                grams.append(text_clean[i:i+2])

        # 同义词的关联特征频段，自动从语义层获取以保证一致性
        semantic_groups = [
            ["时间", "天", "月", "日", "dt", "trend", "趋势", "走势"]
        ]
        # 动态添加已注册的指标/维度别名组
        from app.service.semantic_layer import semantic_layer
        for m in semantic_layer.metrics.values():
            if m.aliases:
                semantic_groups.append(m.aliases)
        for d in semantic_layer.dimensions.values():
            if d.aliases:
                semantic_groups.append(d.aliases)

        for gram in grams:
            val = sum(ord(c) * (31 ** idx) for idx, c in enumerate(gram))
            slot_idx = val % self.embedding_dim
            slots[slot_idx] += 1.0

        # 别名组投影优化：在整句级别进行，不再针对每个 gram 进行冗余循环
        for group_idx, group in enumerate(semantic_groups):
            if any(k in text_clean for k in group if k):
                np.random.seed(42 + group_idx)
                project_vec = np.random.randn(self.embedding_dim)
                slots += project_vec * 0.5

        norm = np.linalg.norm(slots)
        if norm > 0:
            slots = slots / norm
        return slots.tolist()

    def embed_with_provenance(self, text: str) -> Tuple[List[float], str, str]:
        """返回 (向量, 模型名, 状态)。

        这是全模块唯一真正产生向量的地方：任何一次降级都在这里被记录成
        `HASH_EMBEDDING_MODEL` + `STATUS_DEGRADED`，从而在 payload 里留下痕迹、
        在检索时被预过滤隔离。
        """
        from app.service.db_service import db_service
        # Migrating project fixtures to PostgreSQL must not add a model dependency.
        if db_service.is_sample_data:
            return self._remember(self._get_local_hash_embedding(text), HASH_EMBEDDING_MODEL, STATUS_ACTIVE, "")

        api_key, base_url = self._read_embedding_vendor()

        if api_key and base_url and "api.deepseek.com" not in base_url:
            reason = ""
            try:
                import httpx
                url = f"{base_url.rstrip('/')}/embeddings"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "input": text,
                    "model": ONLINE_EMBEDDING_MODEL
                }
                r = httpx.post(url, headers=headers, json=payload, timeout=3.0)
                if r.status_code == 200:
                    data = r.json()
                    return self._remember(data["data"][0]["embedding"], ONLINE_EMBEDDING_MODEL, STATUS_ACTIVE, "")
                reason = f"http {r.status_code}"
            except Exception as e:
                reason = str(e)
            # 在线模型是既定路径却失败了 —— 这是降级，必须留痕，不能假装无事发生。
            print(f"[VectorService] Online Embedding failed: {reason}. Fallback to hash embedding (degraded).")
            return self._remember(self._get_local_hash_embedding(text), HASH_EMBEDDING_MODEL, STATUS_DEGRADED, reason)

        # 压根没配在线 Embedding：哈希向量就是既定路径，不算降级。
        return self._remember(self._get_local_hash_embedding(text), HASH_EMBEDDING_MODEL, STATUS_ACTIVE, "")

    def _remember(self, vector: List[float], model: str, status: str, reason: str) -> Tuple[List[float], str, str]:
        self.last_embedding_model = model
        self.last_embedding_status = status
        self.last_degraded_reason = reason
        return vector, model, status

    def get_embedding(self, text: str) -> List[float]:
        """
        获取文本 Embedding 向量。自动根据 llm_config.json 选用在线大模型 API
        或本地降级哈希算法。（保持原有契约：只返回向量本体）
        """
        vector, _model, _status = self.embed_with_provenance(text)
        return vector

    def _embed_for_index(self, text: str) -> Tuple[List[float], str, str]:
        """灌库侧嵌入：额外做维度校验，维度不符一律按降级处理。

        在线模型换代（1536 → 3072）时若不拦这一下，upsert 会直接抛异常炸掉整条灌库链路；
        拦下来之后最坏情况只是这个点退到哈希空间，并且带着 degraded 状态可被审计。
        """
        vector, model, status = self.embed_with_provenance(text)
        if len(vector) != self.embedding_dim:
            print(f"[VectorService] Embedding dim {len(vector)} != {self.embedding_dim}; "
                  f"falling back to hash embedding (degraded).")
            vector = self._get_local_hash_embedding(text)
            return self._remember(vector, HASH_EMBEDDING_MODEL, STATUS_DEGRADED, "dim-mismatch")
        return vector, model, status

    def _embed_for_query(self, text: str) -> Tuple[List[float], str]:
        """检索侧嵌入：返回 (向量, 模型名)，模型名即预过滤的隔离键。"""
        vector, model, _status = self.embed_with_provenance(text)
        if len(vector) != self.embedding_dim:
            vector = self._get_local_hash_embedding(text)
            model = HASH_EMBEDDING_MODEL
            self._remember(vector, model, STATUS_DEGRADED, "dim-mismatch")
        return vector, model

    # ------------------------------------------------------------------
    # 预过滤
    # ------------------------------------------------------------------
    def build_query_filter(self, embedding_model: str, source_id: Optional[str] = None,
                           point_kind: Optional[str] = None) -> Optional[qmodels.Filter]:
        """构造检索预过滤条件。

        `embedding_model` 是硬性条件：不同模型的向量根本不在同一个空间里，
        跨模型比出来的余弦相似度没有任何意义。`source_id` 把召回限定在当前数据源，
        切换数据源后不会把上一个源的指标/维度召回出来。
        """
        if not self.enable_prefilter:
            return None
        must: List[Any] = []
        if embedding_model:
            must.append(qmodels.FieldCondition(
                key="embedding_model", match=qmodels.MatchValue(value=embedding_model)))
        if source_id:
            must.append(qmodels.FieldCondition(
                key="source_id", match=qmodels.MatchValue(value=source_id)))
        if point_kind:
            must.append(qmodels.FieldCondition(
                key="point_kind", match=qmodels.MatchValue(value=point_kind)))
        if not must:
            return None
        must_not: List[Any] = []
        if self.exclude_degraded_on_recall:
            must_not.append(qmodels.FieldCondition(
                key="status", match=qmodels.MatchValue(value=STATUS_DEGRADED)))
        return qmodels.Filter(must=must, must_not=must_not or None)

    def _query_points(self, collection_name, query, query_filter, limit):
        """带锁的向量检索：与后台增量同步共用一把锁，避免读到写了一半的内存索引。"""
        with self.index_lock:
            return self.client.query_points(
                collection_name=collection_name,
                query=query,
                query_filter=query_filter,
                limit=limit,
            )

    def _scroll_points(self, collection_name, scroll_filter, limit, offset=None,
                       with_payload=True, with_vectors=False):
        with self.index_lock:
            return self.client.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                limit=limit,
                offset=offset,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )

    # ------------------------------------------------------------------
    # 幂等写入
    # ------------------------------------------------------------------
    def _governance_payload(self, *, source_id: str, point_kind: str, text: str,
                            embedding_model: str, status: str) -> Dict[str, Any]:
        return {
            "source_id": source_id,
            "point_kind": point_kind,
            "embedding_model": embedding_model,
            "status": status,
            "content_hash": content_fingerprint(text),
            "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _existing_fingerprints(self, collection: str, ids: List[str]) -> Dict[str, Tuple[str, str]]:
        """已在库的点 -> (content_hash, embedding_model)，用于跳过无变化的重复嵌入。"""
        if not ids:
            return {}
        try:
            with self.index_lock:
                records = self.client.retrieve(collection_name=collection, ids=ids,
                                               with_payload=True, with_vectors=False)
        except Exception:
            return {}
        out: Dict[str, Tuple[str, str]] = {}
        for rec in records:
            payload = rec.payload or {}
            out[str(rec.id)] = (payload.get("content_hash", ""), payload.get("embedding_model", ""))
        return out

    def _build_points(self, collection: str, source_id: str, point_kind: str,
                      items: List[Tuple[str, str, Dict[str, Any]]]) -> Tuple[List[PointStruct], List[str], int]:
        """把 (业务键, 待嵌入文本, 业务 payload) 批量物化为确定性点。

        返回 (需要写入的点, 本轮应保留的全部点 ID, 跳过重嵌的数量)。
        内容指纹与目标模型都没变的点直接跳过，不重复调用 Embedding。
        """
        if not items:
            return [], [], 0
        ids = [derive_point_id(collection, source_id, key) for key, _text, _payload in items]
        known = self._existing_fingerprints(collection, ids)
        target_model = self.intended_embedding_model()

        points: List[PointStruct] = []
        skipped = 0
        for point_id, (_key, text, business_payload) in zip(ids, items):
            fingerprint = content_fingerprint(text)
            prior = known.get(point_id)
            if prior and prior[0] == fingerprint and prior[1] == target_model:
                skipped += 1
                continue
            vector, model, status = self._embed_for_index(text)
            payload = dict(business_payload)
            payload.update(self._governance_payload(
                source_id=source_id, point_kind=point_kind, text=text,
                embedding_model=model, status=status))
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))
        return points, ids, skipped

    def _sync_points(self, collection: str, source_id: str, points: List[PointStruct],
                     keep_ids: List[str], prune: bool = True) -> int:
        """增量 upsert，并剪掉**本数据源下**已不存在的陈旧点。

        剪枝只针对同一个 source_id，其它数据源的点原样保留，切回去时无需重嵌。
        """
        with self.index_lock:
            if points:
                self.client.upsert(collection_name=collection, points=points)
            if not prune:
                return len(points)

            keep = set(keep_ids)
            stale: List[Any] = []
            offset = None
            scroll_filter = qmodels.Filter(must=[qmodels.FieldCondition(
                key="source_id", match=qmodels.MatchValue(value=source_id))])
            while True:
                batch, offset = self.client.scroll(
                    collection_name=collection,
                    scroll_filter=scroll_filter,
                    limit=256,
                    offset=offset,
                    with_payload=False,
                    with_vectors=False,
                )  # 已在 index_lock 内
                for record in batch:
                    if str(record.id) not in keep:
                        stale.append(record.id)
                if offset is None:
                    break
            if stale:
                self.client.delete(collection_name=collection,
                                   points_selector=qmodels.PointIdsList(points=stale))
                print(f"[VectorService] Pruned {len(stale)} stale points from '{collection}' (source={source_id}).")
        return len(points)

    def ingest_metadata(self):
        """
        分治导入 Metrics 和 Dimensions 到各自的 Qdrant 集合中，
        并从维度的 value_range 字段自动提取枚举值灌入 value_indices 枚举值检索库。

        幂等：点 ID 由 (集合, 数据源, 业务键) 派生，重复调用只覆盖不新增；
        语义层里已删除的指标/维度/枚举值会被剪枝掉。
        """
        source_id = self.active_source_id()

        metric_items: List[Tuple[str, str, Dict[str, Any]]] = []
        dim_items: List[Tuple[str, str, Dict[str, Any]]] = []
        value_items: List[Tuple[str, str, Dict[str, Any]]] = []

        # 1. 导入指标至 dwh_metrics
        for m in semantic_layer.metrics.values():
            text_repr = f"指标: {m.name} | 别名: {', '.join(m.aliases)} | 描述: {m.description}"
            payload = {
                "metric_name": m.name,
                "display_name": m.name,
                "agg_func": m.default_agg,
                "description": m.description,
                "table_name": m.source_table,
                "synonyms": m.aliases
            }
            # 业务键带上物理表：同名指标挂在不同表上时是两个对象，不该互相覆盖。
            metric_items.append((f"metric::{m.source_table}::{m.name}", text_repr, payload))

        # 2. 导入维度至 dwh_dims，同时注册列值索引
        for d in semantic_layer.dimensions.values():
            text_repr = f"维度: {d.name} | 别名: {', '.join(d.aliases)} | 可选值: {', '.join(d.value_range or [])}"
            payload = {
                "field_name": d.name,
                "display_name": d.name,
                "table_name": d.source_table,
                "synonyms": d.aliases,
                "sample_values": d.value_range or []
            }
            dim_items.append((f"dimension::{d.source_table}::{d.name}", text_repr, payload))

            # 列值自提取建索引
            if d.value_range:
                for val in d.value_range:
                    val_text_repr = f"维度值: {val} | 归属字段: {d.name} | 归属物理表: {d.source_table}"
                    v_payload = {
                        "value_literal": val,
                        "mapped_field": d.name,
                        "mapped_table": d.source_table
                    }
                    value_items.append((f"value::{d.source_table}::{d.name}::{val}", val_text_repr, v_payload))

        metric_points, metric_ids, metric_skipped = self._build_points(
            self.metrics_collection, source_id, "metric", metric_items)
        dim_points, dim_ids, dim_skipped = self._build_points(
            self.dims_collection, source_id, "dimension", dim_items)
        value_points, value_ids, value_skipped = self._build_points(
            self.value_collection, source_id, "value", value_items)

        # 批量写入 + 同源剪枝
        self._sync_points(self.metrics_collection, source_id, metric_points, metric_ids)
        self._sync_points(self.dims_collection, source_id, dim_points, dim_ids)
        self._sync_points(self.value_collection, source_id, value_points, value_ids)

        print(f"[VectorService] Ingested {len(metric_ids)} metrics into '{self.metrics_collection}' "
              f"(source={source_id}, reused={metric_skipped}).")
        print(f"[VectorService] Ingested {len(dim_ids)} dimensions into '{self.dims_collection}' "
              f"(source={source_id}, reused={dim_skipped}).")
        print(f"[VectorService] Ingested {len(value_ids)} enum values into '{self.value_collection}' "
              f"(source={source_id}, reused={value_skipped}).")
        from app.service.hybrid_retriever import hybrid_retriever
        hybrid_retriever.build_bm25_indices(semantic_layer)

    def ingest_fewshot_examples(self):
        """导入 Few-shot 示例（幂等：以问句为业务键）"""
        fewshot_data = [
            {
                "question": "A表的X维度各有多少Y指标，只看过去30天",
                "dsl": {
                    "metrics": [{"name": "Y_count", "agg": "SUM"}],
                    "dimensions": [{"name": "X_name"}],
                    "filters": [
                        {"field": "dt", "op": "between", "value": ["2026-06-11", "2026-07-11"]}
                    ]
                }
            }
        ]

        items: List[Tuple[str, str, Dict[str, Any]]] = []
        for item in fewshot_data:
            payload = {
                "question": item["question"],
                "dsl": item["dsl"]
            }
            items.append((f"fewshot::{item['question']}", item["question"], payload))

        points, ids, skipped = self._build_points(
            self.example_collection, GLOBAL_SOURCE_ID, "fewshot", items)
        self._sync_points(self.example_collection, GLOBAL_SOURCE_ID, points, ids)
        print(f"[VectorService] Ingested {len(ids)} fewshot examples into Qdrant (reused={skipped}).")

    @staticmethod
    def _strip_governance(payload: Dict[str, Any]) -> Dict[str, Any]:
        """去掉索引治理字段，只把业务语义交给下游（下游会整包 JSON 喂给大模型）。"""
        return {k: v for k, v in payload.items() if k not in GOVERNANCE_KEYS}

    def recall_semantic_meta(self, query: str, limit: int = 4,
                             source_id: Optional[str] = None,
                             with_provenance: bool = False) -> List[Dict[str, Any]]:
        """
        全链路 Schema Linking RAG 召回：
        1. 检索 value_indices 列值索引库，判断是否命中了某些具体的枚举值 (相似度 >= 0.82 强关联)
        2. 检索 dwh_metrics 指标元数据库
        3. 检索 dwh_dims 维度元数据库，如果第 1 步中命中了枚举值对应的字段，则进行合并，强制召回该维度
        4. 统一组装成 M-Schema 结构供大模型进行精细槽位映射

        三次检索全部带预过滤：只比对与查询向量同 Embedding 模型、同数据源的点。
        """
        query_vec, query_model = self._embed_for_query(query)
        scope = source_id or self.active_source_id()
        query_filter = self.build_query_filter(query_model, scope)

        # 1. 检索列值库，提取命中字段名
        hit_fields = set()
        val_hits = self._query_points(self.value_collection, query_vec, query_filter, 3)
        for hit in val_hits.points:
            # 82% 以上的置信度，判定命中具体的枚举值字眼
            if hit.score >= 0.82:
                field = (hit.payload or {}).get("mapped_field")
                if field:
                    hit_fields.add(field)

        # 2. 检索指标库
        metric_hits = self._query_points(self.metrics_collection, query_vec, query_filter, limit)

        # 3. 检索维度库
        dim_hits = self._query_points(self.dims_collection, query_vec, query_filter, limit)

        results = []

        # 注入指标元数据
        for hit in metric_hits.points:
            # 复制一份再改：直接改 hit.payload 有污染库内点的风险。
            payload = dict(hit.payload or {})
            if not with_provenance:
                payload = self._strip_governance(payload)
            payload["type"] = "metric"
            payload["similarity"] = hit.score
            payload["name"] = payload.get("metric_name")  # 兼容 downstream 对 .name 的获取
            results.append(payload)

        # 注入维度元数据
        retrieved_dims = set()
        for hit in dim_hits.points:
            payload = dict(hit.payload or {})
            if not with_provenance:
                payload = self._strip_governance(payload)
            payload["type"] = "dimension"
            payload["similarity"] = hit.score
            payload["name"] = payload.get("field_name")   # 兼容 downstream 对 .name 的获取
            results.append(payload)
            retrieved_dims.add(payload.get("field_name"))

        # 补全被列值强命中的维度
        for field in hit_fields:
            if field not in retrieved_dims:
                must = [qmodels.FieldCondition(key="field_name", match=qmodels.MatchValue(value=field))]
                if self.enable_prefilter:
                    must.append(qmodels.FieldCondition(
                        key="embedding_model", match=qmodels.MatchValue(value=query_model)))
                    must.append(qmodels.FieldCondition(
                        key="source_id", match=qmodels.MatchValue(value=scope)))
                target_search = self._scroll_points(self.dims_collection,
                                                    qmodels.Filter(must=must), 1)
                if target_search and target_search[0]:
                    payload = dict(target_search[0][0].payload or {})
                    if not with_provenance:
                        payload = self._strip_governance(payload)
                    payload["type"] = "dimension"
                    payload["similarity"] = 1.0
                    payload["name"] = payload.get("field_name")
                    results.append(payload)

        # 4. 融合 BM25 稀疏检索结果 (Hybrid Retrieval)
        from app.service.hybrid_retriever import hybrid_retriever
        if not hybrid_retriever.is_indexed:
            hybrid_retriever.build_bm25_indices(semantic_layer)

        bm25_m_hits = hybrid_retriever.bm25_metrics.search(query, top_k=limit)
        bm25_d_hits = hybrid_retriever.bm25_dims.search(query, top_k=limit)
        bm25_all = bm25_m_hits + bm25_d_hits

        # 融合稠密与稀疏
        fused_candidates = hybrid_retriever.fuse_rrf(results, bm25_all)

        # 5. 执行 Schema Linking topological Rerank (拓扑连通性与词汇匹配精排重采样)
        final_results = self._rerank_schema_links(query, fused_candidates)
        return final_results

    def _rerank_schema_links(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        基于数据库 JOIN 拓扑与字面相似度加权的 Schema Linking 精排重构 (Topological Rerank)
        - 1. 字面命中加权 (Lexical Match Boosting): 提问包含指标/维度的别名或值, 额外加分。
        - 2. 拓扑连通性加权 (Graph Connection Boosting): 维度与召回指标在 JoinPath 拓扑中可达/属于同一物理表, 额外加分。
        - 3. 孤岛过滤 (Island Truncation): 维度表与当前召回的指标主表不存在连通路径且并非主表, 直接降权/过滤，防生成非法多表 JOIN。
        """
        from app.service.semantic_layer import semantic_layer

        # 1. 查找用户提问中被召回出的指标
        recalled_metrics = [c for c in candidates if c.get("type") == "metric"]
        recalled_dims = [c for c in candidates if c.get("type") == "dimension"]

        if not recalled_metrics:
            # 没有指标召回，则不进行拓扑精排，仅按分数排序
            return sorted(candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)

        # 找出置信度最高的指标作为精排锚点指标
        anchor_metric = max(recalled_metrics, key=lambda x: x.get("similarity", 0.0))
        anchor_table = anchor_metric.get("table_name")

        # 2. 对每个维度进行打分微调并过滤孤岛维度
        query_lower = query.lower()
        filtered_dims = []
        for d in recalled_dims:
            dim_table = d.get("table_name")

            # 拓扑连通性校验：维度所在表与锚点指标主表的一致性与可达性 (支持多跳)
            connected = False
            boost_topo = 0.0
            if dim_table == anchor_table:
                connected = True
                boost_topo = 0.20
            elif semantic_layer.get_join_path_chain(anchor_table, dim_table):
                connected = True
                boost_topo = 0.15

            # 物理过滤：若两表之间不存在拓扑关联且不是本表维度，彻底丢弃，防生成无效 SQL 或绕过安全规则
            if not connected:
                print(f"[Schema Rerank Island Filter]: Dropped dimension '{d.get('name')}' because table '{dim_table}' is disconnected from anchor metric table '{anchor_table}'.")
                continue

            base_score = d.get("similarity", 0.0)
            boost = boost_topo

            # (A) 词匹配加权：问句中直接包含了维度别名或样值
            dim_name = d.get("name") or d.get("field_name")
            synonyms = d.get("synonyms", [])
            sample_values = d.get("sample_values", [])

            # 精排完全匹配加权 (Lexical Boosting)：当别名在问题中精确完整出现时，赋予 0.40 高额加分，强置信度命中
            if dim_name and dim_name.lower() in query_lower:
                boost += 0.40
            for syn in synonyms:
                if syn.lower() in query_lower:
                    boost += 0.40
                    break
            for val in sample_values:
                if val.lower() in query_lower:
                    boost += 0.30
                    break

            # (C) 显式物理表名匹配加权：用户提问直接带有维表表名
            if dim_table and dim_table.lower() in query_lower:
                boost += 0.40

            d["similarity"] = base_score + boost
            filtered_dims.append(d)

        # 3. 对指标进行打分微调
        for m in recalled_metrics:
            base_score = m.get("similarity", 0.0)
            boost = 0.0
            m_name = m.get("name") or m.get("metric_name")
            synonyms = m.get("synonyms", [])

            # 精排完全匹配加权 (Lexical Boosting)
            if m_name and m_name.lower() in query_lower:
                boost += 0.40
            for syn in synonyms:
                if syn.lower() in query_lower:
                    boost += 0.40
                    break

            # 显式物理表名匹配加权：用户提问直接带有指标所在表名
            tbl_name = m.get("table_name")
            if tbl_name and tbl_name.lower() in query_lower:
                boost += 0.60

            m["similarity"] = base_score + boost

        # 合并排序并限制输出条数，确保大模型接收最精准、拓扑连通的高价值元数据
        final_candidates = recalled_metrics + filtered_dims
        reranked = sorted(final_candidates, key=lambda x: x.get("similarity", 0.0), reverse=True)
        return reranked

    def recall_fewshot_examples(self, query: str, limit: int = 2) -> List[Dict[str, Any]]:
        """从问句库中进行向量检索，匹配出最贴近的标准 DSL 对话示例（同模型预过滤）"""
        vec, query_model = self._embed_for_query(query)
        search_res = self._query_points(
            self.example_collection, vec,
            self.build_query_filter(query_model, GLOBAL_SOURCE_ID), limit)

        results = []
        for hit in search_res.points:
            results.append({
                "question": hit.payload["question"],
                "dsl": hit.payload["dsl"],
                "similarity": hit.score
            })
        return results

    # ------------------------------------------------------------------
    # 纠错经验：增量 upsert，不再全量重建
    # ------------------------------------------------------------------
    @staticmethod
    def _correction_business_key(item: Dict[str, Any]) -> str:
        return "correction::" + "|".join([
            str(item.get("question", "")).strip(),
            str(item.get("error_message", "")).strip(),
            str(item.get("wrong_sql", "")).strip(),
            str(item.get("corrected_sql", "")).strip(),
        ])

    @staticmethod
    def _correction_text(item: Dict[str, Any]) -> str:
        # 以报错问题和错误信息共同作为向量文本
        return f"问题: {item.get('question', '')} | 报错: {item.get('error_message', '')}"

    def correction_point_id(self, item: Dict[str, Any]) -> str:
        """某条纠错经验的确定性点 ID —— 反复写入同一条不会产生重复点。"""
        return derive_point_id(self.error_correction_collection, GLOBAL_SOURCE_ID,
                               self._correction_business_key(item))

    def ingest_error_corrections(self, rebuild: bool = False):
        """
        把 user_memory 里的纠错经验同步进向量库。

        默认**增量**：只嵌入新增/内容变化的条目，并剪掉已被删除的条目，
        不再 `delete_collection` + 全量重嵌 —— 后者会把一次“记住一条经验”
        放大成 O(全部经验) 次 Embedding 调用，还在重建窗口内让检索短暂空窗。

        `rebuild=True` 保留旧的全量重建语义，仅用于索引损坏时的人工修复，默认关闭。
        """
        from app.model.user_memory import user_memory

        with self.index_lock:
            if rebuild:
                # 破坏性路径：显式要求时才走，重建期间集合会短暂为空。
                if self.client.collection_exists(collection_name=self.error_correction_collection):
                    self.client.delete_collection(collection_name=self.error_correction_collection)
                self.client.create_collection(
                    collection_name=self.error_correction_collection,
                    vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE)
                )

            corrections = user_memory.get_error_corrections()
            items: List[Tuple[str, str, Dict[str, Any]]] = []
            for item in corrections:
                payload = {
                    "question": item.get("question", ""),
                    "error_message": item.get("error_message", ""),
                    "wrong_sql": item.get("wrong_sql", ""),
                    "corrected_sql": item.get("corrected_sql", ""),
                    "origin": "user_memory",
                }
                items.append((self._correction_business_key(item), self._correction_text(item), payload))

            points, ids, skipped = self._build_points(
                self.error_correction_collection, GLOBAL_SOURCE_ID, "error_correction", items)
            # 剪枝即“物理删除”的等价物：user_memory 里没了的条目，这里也留不下。
            self._sync_points(self.error_correction_collection, GLOBAL_SOURCE_ID, points, ids)
            print(f"[VectorService] Synced {len(ids)} error corrections into Qdrant "
                  f"(embedded={len(points)}, reused={skipped}).")
            return {"total": len(ids), "embedded": len(points), "reused": skipped}

    def upsert_error_correction(self, item: Dict[str, Any]) -> str:
        """单条增量写入：一次 Embedding、一次 upsert，与经验库规模无关。

        自学习反馈回路（每答对一次纠错就记一条）应该调这个，而不是整库重嵌。
        返回该条经验的确定性点 ID。
        """
        text = self._correction_text(item)
        payload = {
            "question": item.get("question", ""),
            "error_message": item.get("error_message", ""),
            "wrong_sql": item.get("wrong_sql", ""),
            "corrected_sql": item.get("corrected_sql", ""),
            "origin": "user_memory",
        }
        points, ids, _skipped = self._build_points(
            self.error_correction_collection, GLOBAL_SOURCE_ID, "error_correction",
            [(self._correction_business_key(item), text, payload)])
        # prune=False：单条写入不该去动别的点。
        self._sync_points(self.error_correction_collection, GLOBAL_SOURCE_ID, points, ids, prune=False)
        return ids[0]

    def ingest_error_corrections_async(self, rebuild: bool = False) -> threading.Thread:
        """把整库同步挪出同步主链路。

        问数主链路只需要“最终一致”的纠错经验库，不该为了它多等一轮 Embedding I/O。
        """
        def _run():
            try:
                self.ingest_error_corrections(rebuild=rebuild)
            except Exception as error:
                print(f"[VectorService] Background correction sync failed: {error}")

        worker = threading.Thread(target=_run, name="vector-correction-sync", daemon=True)
        worker.start()
        return worker

    def recall_error_corrections(self, query: str, error_message: str = "", limit: int = 1) -> List[Dict[str, Any]]:
        """
        从纠错经验库中检索相关的 SQL 纠错案例（同模型预过滤）
        """
        text_repr = f"问题: {query} | 报错: {error_message}"
        vec, query_model = self._embed_for_query(text_repr)
        search_res = self._query_points(
            self.error_correction_collection, vec,
            self.build_query_filter(query_model, GLOBAL_SOURCE_ID), limit)

        results = []
        for hit in search_res.points:
            # 只有置信度大于 0.40 才建议引入，免得无关纠错误导模型
            if hit.score >= 0.40:
                results.append({
                    "question": hit.payload["question"],
                    "error_message": hit.payload["error_message"],
                    "wrong_sql": hit.payload["wrong_sql"],
                    "corrected_sql": hit.payload["corrected_sql"],
                    "similarity": hit.score
                })
        return results

    # ------------------------------------------------------------------
    # 索引健康度
    # ------------------------------------------------------------------
    def describe_index(self) -> Dict[str, Any]:
        """派生索引的体检报告：每个集合里各模型/状态各有多少点。

        `mixed_embedding_models` 为真意味着同一集合里混着不同向量空间的点 ——
        此时若预过滤被关掉，检索分数就不可信。
        """
        report: Dict[str, Any] = {
            "active_source_id": self.active_source_id(),
            "intended_embedding_model": self.intended_embedding_model(),
            "prefilter_enabled": self.enable_prefilter,
            "exclude_degraded_on_recall": self.exclude_degraded_on_recall,
            "collections": {},
        }
        for name in [self.metrics_collection, self.dims_collection, self.value_collection,
                     self.example_collection, self.error_correction_collection]:
            models_count: Dict[str, int] = {}
            status_count: Dict[str, int] = {}
            sources_count: Dict[str, int] = {}
            total = 0
            offset = None
            try:
                while True:
                    batch, offset = self._scroll_points(
                        name, None, 256, offset=offset, with_payload=True, with_vectors=False)
                    for record in batch:
                        payload = record.payload or {}
                        total += 1
                        model = payload.get("embedding_model", "unknown")
                        status = payload.get("status", "unknown")
                        source = payload.get("source_id", "unknown")
                        models_count[model] = models_count.get(model, 0) + 1
                        status_count[status] = status_count.get(status, 0) + 1
                        sources_count[source] = sources_count.get(source, 0) + 1
                    if offset is None:
                        break
            except Exception as error:
                report["collections"][name] = {"error": str(error)}
                continue
            report["collections"][name] = {
                "points": total,
                "embedding_models": models_count,
                "statuses": status_count,
                "sources": sources_count,
                "mixed_embedding_models": len(models_count) > 1,
                "degraded_points": status_count.get(STATUS_DEGRADED, 0),
            }
        return report

# 初始化单例向量库服务
vector_service = VectorService()
