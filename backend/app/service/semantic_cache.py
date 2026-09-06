# -*- coding: utf-8 -*-
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from app.schema.chat import AskResponse

logger = logging.getLogger(__name__)

# =====================================================================
# 多级语义缓存服务 (Semantic Query Cache)
# 针对智能问数场景，解决大模型生成 SQL 延迟高、高频相似查询重复消耗 Token 痛点。
# 架构设计：
# 1. L1 精确哈希缓存 (Exact Query Hash Match): 基于角色、方言与清洗后问句的 SHA-256，O(1) 毫秒级命中 (<5ms)
# 2. L2 语义向量缓存 (Semantic Vector Match): 基于 Embedding 余弦相似度，相似度 >= 0.96 视为等价意图，直接复用 DSL 与计算结果
# 3. TTL 生命周期淘汰与主动失效机制 (Cache Invalidation)
# =====================================================================

class CacheItem:
    def __init__(self, key: str, question: str, response_data: Dict[str, Any], embedding: Optional[List[float]] = None, ttl_seconds: int = 3600, dialect: str = "doris", role: str = "user"):
        self.key = key
        self.question = question
        self.response_data = response_data
        self.embedding = embedding
        self.dialect = dialect
        self.role = role
        self.created_at = time.time()
        self.expire_at = self.created_at + ttl_seconds
        self.hit_count = 0

    def is_expired(self) -> bool:
        return time.time() > self.expire_at


class SemanticCache:
    def __init__(self, default_ttl: int = 3600, similarity_threshold: float = 0.985):
        """
        初始化语义缓存池
        :param default_ttl: 缓存默认过期时间（秒），默认 1 小时
        :param similarity_threshold: 语义匹配相似度门限，默认 0.985，确保意图高度一致
        """
        self.default_ttl = default_ttl
        self.similarity_threshold = similarity_threshold
        # L1 精确缓存字典
        self.exact_cache: Dict[str, CacheItem] = {}
        # L2 语义项列表
        self.semantic_items: List[CacheItem] = []
        # 性能统计指标
        self.total_requests = 0
        self.exact_hits = 0
        self.semantic_hits = 0

    @staticmethod
    def _generate_key(question: str, dialect: str, role: str) -> str:
        """
        规范化问句并生成稳定的 SHA-256 唯一缓存键
        """
        normalized_q = question.strip().lower().replace(" ", "")
        raw_token = f"{role.lower()}::{dialect.lower()}::{normalized_q}"
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """
        计算两个归一化向量的余弦相似度
        """
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a, b in zip(vec_a, vec_b)) ** 0.5
        norm_b = sum(b * b for a, b in zip(vec_a, vec_b)) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    @staticmethod
    def _validated_response(response_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Only reuse serializable results that satisfy the public response contract."""
        try:
            response = AskResponse.model_validate(response_data)
            if not response.success or (
                response.clarification and response.clarification.need_clarification
            ):
                return None
            # Serialization makes an independent copy of nested data and chart config.
            return response.model_dump(mode="json", exclude_unset=True)
        except (ValidationError, PydanticSerializationError, ValueError):
            logger.warning("忽略不符合问数响应协议的缓存结果，后续请求将重新执行查询。")
            return None

    def _discard(self, key: str) -> None:
        """Remove an expired, invalid or replaced result from both cache tiers."""
        self.exact_cache.pop(key, None)
        self.semantic_items = [item for item in self.semantic_items if item.key != key]

    def get(self, question: str, dialect: str = "doris", role: str = "user", query_embedding: Optional[List[float]] = None) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        多级缓存检索：
        :return: (response_data, hit_type) 或 None。hit_type: "exact" 或 "semantic"
        """
        self.total_requests += 1
        key = self._generate_key(question, dialect, role)

        # 1. 优先检索 L1 精确哈希匹配
        if key in self.exact_cache:
            item = self.exact_cache[key]
            resp = None if item.is_expired() else self._validated_response(item.response_data)
            if resp is not None:
                item.hit_count += 1
                self.exact_hits += 1
                logger.info("语义缓存 L1 [Exact Match] 命中: '%s' (总命中次数: %d)", question, item.hit_count)
                # 深拷贝返回，并标注缓存命中标识
                resp["cache_hit"] = True
                resp["cache_type"] = "exact"
                return resp, "exact"
            else:
                self._discard(key)

        # 2. 检索 L2 语义向量缓存
        if query_embedding and len(query_embedding) > 0:
            best_sim = 0.0
            best_item: Optional[CacheItem] = None
            best_response: Optional[Dict[str, Any]] = None
            valid_items: List[CacheItem] = []

            for item in self.semantic_items:
                if item.is_expired():
                    self.exact_cache.pop(item.key, None)
                    continue
                if item.role.lower() != role.lower() or item.dialect.lower() != dialect.lower():
                    valid_items.append(item)
                    continue
                if item.embedding:
                    sim = self._cosine_similarity(query_embedding, item.embedding)
                    if sim > best_sim and sim >= self.similarity_threshold:
                        resp = self._validated_response(item.response_data)
                        if resp is None:
                            self.exact_cache.pop(item.key, None)
                            continue
                        best_sim = sim
                        best_item = item
                        best_response = resp
                valid_items.append(item)

            # 淘汰已过期的语义项
            self.semantic_items = valid_items

            if best_item is not None and best_response is not None:
                # 语义意图对齐防护：若一个包含分组/多维关键词而另一个不包含，拒绝误判
                group_indicators = ["各", "每", "分别", "按", "品类", "区域", "排行", "top", "前三", "前3", "前10"]
                q_has_group = any(w in question for w in group_indicators)
                base_has_group = any(w in best_item.question for w in group_indicators)
                if q_has_group != base_has_group:
                    return None

                # 语义实体过滤条件对齐：若涉及不同地区、时间或指标限定，拒绝误匹配
                entity_keywords = [
                    "华东", "华北", "华南", "华中", "西南", "西北", "东北",
                    "昨天", "今天", "前天", "上月", "上个月", "本月", "上周", "30天", "7天",
                    "退款", "交易额", "销售额", "gmv", "订单", "单量"
                ]
                for kw in entity_keywords:
                    if (kw in question) != (kw in best_item.question):
                        return None

                best_item.hit_count += 1
                self.semantic_hits += 1
                logger.info(
                    "语义缓存 L2 [Semantic Match] 命中! 当前问题: '%s', 匹配基线: '%s', 相似度: %.4f",
                    question, best_item.question, best_sim
                )
                resp = best_response
                resp["cache_hit"] = True
                resp["cache_type"] = "semantic"
                resp["matched_question"] = best_item.question
                resp["similarity_score"] = round(best_sim, 4)
                return resp, "semantic"

        return None

    def put(self, question: str, dialect: str, role: str, response_data: Dict[str, Any], embedding: Optional[List[float]] = None, ttl_seconds: Optional[int] = None) -> None:
        """
        写入多级语义缓存
        """
        validated_response = self._validated_response(response_data)
        if validated_response is None:
            return

        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        key = self._generate_key(question, dialect, role)
        item = CacheItem(
            key=key,
            question=question,
            response_data=validated_response,
            embedding=list(embedding) if embedding else None,
            ttl_seconds=ttl,
            dialect=dialect,
            role=role
        )

        # 写入 L1 精确缓存
        self._discard(key)
        self.exact_cache[key] = item

        # 写入 L2 语义缓存（限制最大缓存条数，LRU 策略防内存膨胀）
        if embedding:
            if len(self.semantic_items) >= 500:
                # 剔除访问最少或最早的项
                self.semantic_items.sort(key=lambda x: (x.hit_count, x.created_at))
                self.semantic_items.pop(0)
            self.semantic_items.append(item)

        logger.info("写入语义缓存成功: '%s' (有效TTL: %ds)", question, ttl)

    def invalidate_all(self) -> None:
        """
        清空全量缓存（元数据变更或表 DDL 刷新时触发）
        """
        self.exact_cache.clear()
        self.semantic_items.clear()
        logger.info("语义缓存全量已清空重置。")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取当前缓存池命中率及状态统计
        """
        total_hits = self.exact_hits + self.semantic_hits
        hit_ratio = round(total_hits / max(self.total_requests, 1) * 100, 2)
        
        # 收集非过期的缓存条目摘要（最近 20 条）
        entries = []
        now = time.time()
        for item in list(self.exact_cache.values())[:20]:
            if not item.is_expired():
                entries.append({
                    "key": item.key[:10] + "...",
                    "question": item.question,
                    "role": item.role,
                    "dialect": item.dialect,
                    "hit_count": item.hit_count,
                    "ttl_remaining_sec": max(0, int(item.expire_at - now)),
                    "has_embedding": item.embedding is not None
                })

        return {
            "total_requests": self.total_requests,
            "total_hits": total_hits,
            "hit_ratio_percent": hit_ratio,
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "cached_exact_count": len(self.exact_cache),
            "cached_semantic_count": len(self.semantic_items),
            "cached_entries": entries
        }

# Only executed query results enter the cache; startup must not invent answers.
semantic_cache = SemanticCache()
