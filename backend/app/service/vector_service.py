# -*- coding: utf-8 -*-
import os
import re
import json
import numpy as np
from typing import List, Dict, Any, Tuple
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from app.service.semantic_layer import semantic_layer

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

    def get_embedding(self, text: str) -> List[float]:
        """
        获取文本 Embedding 向量。自动根据 llm_config.json 选用在线大模型 API 
        或本地降级哈希算法。
        """
        config_path = "/Users/mindezhi/DataWareHouse-Agent/backend/llm_config.json"
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
        except:
            pass
            
        if api_key and base_url and "api.deepseek.com" not in base_url:
            try:
                import httpx
                url = f"{base_url.rstrip('/')}/embeddings"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "input": text,
                    "model": "text-embedding-ada-002"
                }
                r = httpx.post(url, headers=headers, json=payload, timeout=3.0)
                if r.status_code == 200:
                    data = r.json()
                    return data["data"][0]["embedding"]
            except Exception as e:
                print(f"[VectorService] Online Embedding failed: {e}. Fallback to hash embedding.")
                
        return self._get_local_hash_embedding(text)

    def ingest_metadata(self):
        """
        分治导入 Metrics 和 Dimensions 到各自的 Qdrant 集合中，
        并从维度的 value_range 字段自动提取枚举值灌入 value_indices 枚举值检索库。
        """
        metric_points = []
        dim_points = []
        value_points = []
        
        m_id = 1
        d_id = 1
        v_id = 1
        
        # 1. 导入指标至 dwh_metrics
        for m in semantic_layer.metrics.values():
            text_repr = f"指标: {m.name} | 别名: {', '.join(m.aliases)} | 描述: {m.description}"
            vec = self.get_embedding(text_repr)
            
            payload = {
                "metric_name": m.name,
                "display_name": m.name,
                "agg_func": m.default_agg,
                "description": m.description,
                "table_name": m.source_table,
                "synonyms": m.aliases
            }
            metric_points.append(PointStruct(id=m_id, vector=vec, payload=payload))
            m_id += 1
            
        # 2. 导入维度至 dwh_dims，同时注册列值索引
        for d in semantic_layer.dimensions.values():
            text_repr = f"维度: {d.name} | 别名: {', '.join(d.aliases)} | 可选值: {', '.join(d.value_range or [])}"
            vec = self.get_embedding(text_repr)
            
            payload = {
                "field_name": d.name,
                "display_name": d.name,
                "table_name": d.source_table,
                "synonyms": d.aliases,
                "sample_values": d.value_range or []
            }
            dim_points.append(PointStruct(id=d_id, vector=vec, payload=payload))
            d_id += 1
            
            # 列值自提取建索引
            if d.value_range:
                for val in d.value_range:
                    val_text_repr = f"维度值: {val} | 归属字段: {d.name} | 归属物理表: {d.source_table}"
                    v_vec = self.get_embedding(val_text_repr)
                    v_payload = {
                        "value_literal": val,
                        "mapped_field": d.name,
                        "mapped_table": d.source_table
                    }
                    value_points.append(PointStruct(id=v_id, vector=v_vec, payload=v_payload))
                    v_id += 1

        # 批量写入
        if metric_points:
            self.client.upsert(collection_name=self.metrics_collection, points=metric_points)
        if dim_points:
            self.client.upsert(collection_name=self.dims_collection, points=dim_points)
        if value_points:
            self.client.upsert(collection_name=self.value_collection, points=value_points)
            
        print(f"[VectorService] Ingested {len(metric_points)} metrics into '{self.metrics_collection}'.")
        print(f"[VectorService] Ingested {len(dim_points)} dimensions into '{self.dims_collection}'.")
        print(f"[VectorService] Ingested {len(value_points)} enum values into '{self.value_collection}'.")

    def ingest_fewshot_examples(self):
        """导入 Few-shot 示例"""
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
        
        points = []
        point_id = 1000
        for item in fewshot_data:
            vec = self.get_embedding(item["question"])
            payload = {
                "question": item["question"],
                "dsl": item["dsl"]
            }
            points.append(PointStruct(id=point_id, vector=vec, payload=payload))
            point_id += 1
            
        if points:
            self.client.upsert(collection_name=self.example_collection, points=points)
            print(f"[VectorService] Ingested {len(points)} fewshot examples into Qdrant.")

    def recall_semantic_meta(self, query: str, limit: int = 4) -> List[Dict[str, Any]]:
        """
        全链路 Schema Linking RAG 召回：
        1. 检索 value_indices 列值索引库，判断是否命中了某些具体的枚举值 (相似度 >= 0.82 强关联)
        2. 检索 dwh_metrics 指标元数据库
        3. 检索 dwh_dims 维度元数据库，如果第 1 步中命中了枚举值对应的字段，则进行合并，强制召回该维度
        4. 统一组装成 M-Schema 结构供大模型进行精细槽位映射
        """
        query_vec = self.get_embedding(query)
        
        # 1. 检索列值库，提取命中字段名
        hit_fields = set()
        val_hits = self.client.query_points(
            collection_name=self.value_collection,
            query=query_vec,
            limit=3
        )
        for hit in val_hits.points:
            # 82% 以上的置信度，判定命中具体的枚举值字眼
            if hit.score >= 0.82:
                field = hit.payload.get("mapped_field")
                if field:
                    hit_fields.add(field)
                    
        # 2. 检索指标库
        metric_hits = self.client.query_points(
            collection_name=self.metrics_collection,
            query=query_vec,
            limit=limit
        )
        
        # 3. 检索维度库
        dim_hits = self.client.query_points(
            collection_name=self.dims_collection,
            query=query_vec,
            limit=limit
        )
        
        results = []
        
        # 注入指标元数据
        for hit in metric_hits.points:
            payload = hit.payload
            payload["type"] = "metric"
            payload["similarity"] = hit.score
            payload["name"] = payload.get("metric_name")  # 兼容 downstream 对 .name 的获取
            results.append(payload)
            
        # 注入维度元数据
        retrieved_dims = set()
        for hit in dim_hits.points:
            payload = hit.payload
            payload["type"] = "dimension"
            payload["similarity"] = hit.score
            payload["name"] = payload.get("field_name")   # 兼容 downstream 对 .name 的获取
            results.append(payload)
            retrieved_dims.add(payload.get("field_name"))
            
        # 补全被列值强命中的维度
        for field in hit_fields:
            if field not in retrieved_dims:
                from qdrant_client.http import models
                target_search = self.client.scroll(
                    collection_name=self.dims_collection,
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="field_name",
                                match=models.MatchValue(value=field)
                            )
                        ]
                    ),
                    limit=1
                )
                if target_search and target_search[0]:
                    payload = target_search[0][0].payload
                    payload["type"] = "dimension"
                    payload["similarity"] = 1.0
                    payload["name"] = payload.get("field_name")
                    results.append(payload)
                    
        # 4. 执行 Schema Linking topological Rerank (拓扑连通性与词汇匹配精排重采样)
        results = self._rerank_schema_links(query, results)
        return results

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
        """从问句库中进行向量检索，匹配出最贴近的标准 DSL 对话示例"""
        vec = self.get_embedding(query)
        search_res = self.client.query_points(
            collection_name=self.example_collection,
            query=vec,
            limit=limit
        )
        
        results = []
        for hit in search_res.points:
            results.append({
                "question": hit.payload["question"],
                "dsl": hit.payload["dsl"],
                "similarity": hit.score
            })
        return results

    def ingest_error_corrections(self):
        """
        从 user_memory 加载所有已存的物理纠错经验并写入 Qdrant 向量库中
        """
        from app.model.user_memory import user_memory
        corrections = user_memory.get_error_corrections()
        points = []
        point_id = 2000
        for item in corrections:
            # 以报错问题和错误信息共同作为向量文本
            text_repr = f"问题: {item['question']} | 报错: {item['error_message']}"
            vec = self.get_embedding(text_repr)
            payload = {
                "question": item["question"],
                "error_message": item["error_message"],
                "wrong_sql": item["wrong_sql"],
                "corrected_sql": item["corrected_sql"]
            }
            points.append(PointStruct(id=point_id, vector=vec, payload=payload))
            point_id += 1
            
        if points:
            self.client.upsert(collection_name=self.error_correction_collection, points=points)
            print(f"[VectorService] Ingested {len(points)} error corrections into Qdrant.")

    def recall_error_corrections(self, query: str, error_message: str = "", limit: int = 1) -> List[Dict[str, Any]]:
        """
        从纠错经验库中检索相关的 SQL 纠错案例
        """
        text_repr = f"问题: {query} | 报错: {error_message}"
        vec = self.get_embedding(text_repr)
        search_res = self.client.query_points(
            collection_name=self.error_correction_collection,
            query=vec,
            limit=limit
        )
        
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

# 初始化单例向量库服务
vector_service = VectorService()
