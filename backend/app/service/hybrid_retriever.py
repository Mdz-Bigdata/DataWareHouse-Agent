# -*- coding: utf-8 -*-
import math
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# 混合检索器 (Hybrid Retriever: BM25 Lexical + Vector Dense + RRF Fusion)
# 对应 ListenBook-DataAgent 与工业级 RAG 最佳实践：
# 纯向量检索（Dense）擅长泛化语义匹配，但在面对特定行业缩写、短英文代码、
# 专有名词（如“GMV”、“华东”、“dt”）时存在漏召回和相似度倒挂问题；
# 纯词法检索（BM25/Sparse）擅长字面精确命中，但无法捕捉同义词与意图跨度。
# 本模块采用 RRF (Reciprocal Rank Fusion, k=60) 融合双路召回，显著提升 Schema Linking 召回率与排序准确度。
# =====================================================================

class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avg_doc_len = 0.0
        self.doc_lens: List[int] = []
        self.doc_tokens: List[List[str]] = []
        self.doc_payloads: List[Dict[str, Any]] = []
        self.inverted_index: Dict[str, List[Tuple[int, int]]] = {}  # term -> [(doc_id, freq)]
        self.idf: Dict[str, float] = {}

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """
        中英文分词：支持中文字符 N-gram (1-gram 与 2-gram) 及英文单词
        """
        text_lower = text.lower().strip()
        tokens = []
        # 英文词与数字匹配
        words = re.findall(r"[a-z0-9_]+", text_lower)
        tokens.extend(words)
        # 中文单字与双字匹配
        chinese_chars = re.findall(r"[\u4e00-\u9fa5]", text_lower)
        tokens.extend(chinese_chars)
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i+1])
        return tokens

    def build_index(self, documents: List[Dict[str, Any]], text_field: str = "search_text") -> None:
        """
        基于文档列表构建 BM25 倒排索引
        """
        self.corpus_size = len(documents)
        self.doc_tokens = []
        self.doc_lens = []
        self.doc_payloads = documents
        self.inverted_index = {}
        total_len = 0

        for doc_id, doc in enumerate(documents):
            text = doc.get(text_field, "")
            tokens = self.tokenize(text)
            self.doc_tokens.append(tokens)
            doc_len = len(tokens)
            self.doc_lens.append(doc_len)
            total_len += doc_len

            # 统计词频
            tf_map: Dict[str, int] = {}
            for t in tokens:
                tf_map[t] = tf_map.get(t, 0) + 1

            for t, freq in tf_map.items():
                if t not in self.inverted_index:
                    self.inverted_index[t] = []
                self.inverted_index[t].append((doc_id, freq))

        self.avg_doc_len = total_len / max(self.corpus_size, 1)

        # 计算 IDF
        self.idf = {}
        for term, postings in self.inverted_index.items():
            df = len(postings)
            self.idf[term] = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1.0)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        """
        对查询计算 BM25 得分并返回 Top-K
        """
        if self.corpus_size == 0:
            return []

        tokens = self.tokenize(query)
        scores: Dict[int, float] = {}

        for t in tokens:
            if t not in self.inverted_index:
                continue
            idf = self.idf.get(t, 0.0)
            for doc_id, freq in self.inverted_index[t]:
                doc_len = self.doc_lens[doc_id]
                denom = freq + self.k1 * (1.0 - self.b + self.b * (doc_len / max(self.avg_doc_len, 1.0)))
                term_score = idf * (freq * (self.k1 + 1.0)) / max(denom, 1e-6)
                scores[doc_id] = scores.get(doc_id, 0.0) + term_score

        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self.doc_payloads[doc_id], score) for doc_id, score in sorted_docs]


class HybridRetriever:
    def __init__(self, rrf_k: int = 60):
        self.rrf_k = rrf_k
        self.bm25_metrics = BM25Index()
        self.bm25_dims = BM25Index()
        self.is_indexed = False

    def build_bm25_indices(self, semantic_layer) -> None:
        """
        从语义层提取指标与维度构建 BM25 倒排索引
        """
        metric_docs = []
        for m in semantic_layer.metrics.values():
            aliases_str = " ".join(m.aliases or [])
            search_text = f"{m.name} {m.description} {aliases_str} {m.source_table}"
            metric_docs.append({
                "type": "metric",
                "name": m.name,
                "metric_name": m.name,
                "table_name": m.source_table,
                "description": m.description,
                "synonyms": m.aliases or [],
                "search_text": search_text
            })
        self.bm25_metrics.build_index(metric_docs)

        dim_docs = []
        for d in semantic_layer.dimensions.values():
            aliases_str = " ".join(d.aliases or [])
            val_str = " ".join(d.value_range or [])
            search_text = f"{d.name} {aliases_str} {val_str} {d.source_table}"
            dim_docs.append({
                "type": "dimension",
                "name": d.name,
                "field_name": d.name,
                "table_name": d.source_table,
                "synonyms": d.aliases or [],
                "sample_values": d.value_range or [],
                "search_text": search_text
            })
        self.bm25_dims.build_index(dim_docs)
        self.is_indexed = True
        logger.info("混合检索器 BM25 倒排索引构建完毕 (指标数: %d, 维度数: %d)", len(metric_docs), len(dim_docs))

    def fuse_rrf(self, dense_results: List[Dict[str, Any]], sparse_results: List[Tuple[Dict[str, Any], float]]) -> List[Dict[str, Any]]:
        """
        使用 RRF (Reciprocal Rank Fusion) 算法融合稠密与稀疏检索排名
        公式: Score_rrf = SUM( 1 / (k + rank_i) )
        """
        score_map: Dict[str, float] = {}
        item_map: Dict[str, Dict[str, Any]] = {}

        # 1. 稠密向量排名加权
        for rank, item in enumerate(dense_results):
            name = item.get("name") or item.get("metric_name") or item.get("field_name")
            if not name:
                continue
            item_map[name] = item
            rrf_contrib = 1.0 / (self.rrf_k + rank + 1)
            score_map[name] = score_map.get(name, 0.0) + rrf_contrib

        # 2. 稀疏 BM25 排名加权
        for rank, (item, _) in enumerate(sparse_results):
            name = item.get("name") or item.get("metric_name") or item.get("field_name")
            if not name:
                continue
            if name not in item_map:
                item_map[name] = item
            rrf_contrib = 1.0 / (self.rrf_k + rank + 1)
            score_map[name] = score_map.get(name, 0.0) + rrf_contrib

        # 排序并重新映射综合相似度分数
        sorted_keys = sorted(score_map.keys(), key=lambda k: score_map[k], reverse=True)
        fused = []
        for k in sorted_keys:
            res_item = dict(item_map[k])
            res_item["rrf_score"] = round(score_map[k], 5)
            # 保留或平滑相似度
            if "similarity" not in res_item or res_item["similarity"] < 0.5:
                res_item["similarity"] = min(0.99, round(score_map[k] * 20.0, 3))
            fused.append(res_item)

        return fused

# 单例导出
hybrid_retriever = HybridRetriever()
