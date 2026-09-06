"""Govern reviewed SQL examples and quarantined auto-repair candidates.

封装经验对的构造、向量召回、成功回写三类操作，供 graph 节点调用。
设计为纯协调层，不持有 HTTP/DB 细节，依赖注入 repository 与 embedding client。

核心流程：
1. Refiner 只召回 lifecycle=published 的人工审核模板。
2. 自动修复成功后只写 lifecycle=candidate，绝不直接进入全局 few-shot。
"""

from __future__ import annotations

import hashlib

from app.core.log import logger
from app.entities.feedback_entry import FeedbackEntry
from app.repositories.qdrant.feedback_qdrant_repository import FeedbackQdrantRepository
from app.services.sql_template_service import (
    build_parameterized_sql_template,
    redact_feedback_text,
)


class FeedbackLearningService:
    """Few-shot 自愈经验的召回与回写。"""

    def __init__(
        self,
        feedback_repository: FeedbackQdrantRepository,
        embedding_client,
    ):
        self.feedback_repository = feedback_repository
        self.embedding_client = embedding_client

    async def recall_similar_fixes(
        self,
        question: str,
        score_threshold: float = 0.7,
        limit: int = 3,
    ) -> list[FeedbackEntry]:
        """召回与当前问题语义相似的历史修复对。

        失败时静默返回空列表（Few-shot 是增强项，不应阻断主流程）。
        """

        if not question or not question.strip():
            return []
        try:
            embedding = await self.embedding_client.aembed_query(question)
            return await self.feedback_repository.search(
                embedding,
                score_threshold=score_threshold,
                limit=limit,
                lifecycle="published",
            )
        except Exception:
            logger.warning("Few-shot 经验召回失败，跳过（不阻断主流程）", exc_info=True)
            return []

    async def record_success_fix(
        self,
        question: str,
        error_sql: str,
        corrected_sql: str,
        error_message: str,
        table_signature: str,
        row_level_scope: list[dict] | None = None,
        dialect: str = "mysql",
    ) -> None:
        """Write a redacted auto-repair candidate for later human review.

        失败时静默（回写是 best-effort，不应影响已成功的查询结果返回）。
        id 用脱敏 question 的 sha256，保证同一问题候选覆盖更新而非无限堆积。
        """

        if not question or not corrected_sql or error_sql == corrected_sql:
            # 无效输入或 SQL 未实际改变，不回写
            return
        try:
            error_template = build_parameterized_sql_template(
                error_sql,
                row_level_scope=row_level_scope,
                dialect=dialect,
            )
            corrected_template = build_parameterized_sql_template(
                corrected_sql,
                row_level_scope=row_level_scope,
                dialect=dialect,
            )
            if not corrected_template.sql or error_template.sql == corrected_template.sql:
                return
            redacted_question = redact_feedback_text(question, max_length=500)
            entry = FeedbackEntry(
                id=hashlib.sha256(redacted_question.encode("utf-8")).hexdigest(),
                question=redacted_question,
                error_sql=error_template.sql,
                corrected_sql=corrected_template.sql,
                error_message=redact_feedback_text(error_message, max_length=1000),
                table_signature=redact_feedback_text(table_signature, max_length=500),
                error_parameter_types=error_template.parameter_types,
                corrected_parameter_types=corrected_template.parameter_types,
                lifecycle="candidate",
                source="auto_repair",
            )
            embedding = await self.embedding_client.aembed_query(redacted_question)
            await self.feedback_repository.upsert(entry, embedding)
        except Exception:
            logger.warning("Few-shot 经验回写失败，跳过（不影响查询结果）", exc_info=True)
