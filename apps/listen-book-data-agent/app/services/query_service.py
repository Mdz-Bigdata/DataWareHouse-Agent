from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.context import get_request_id, set_request_id
from app.core.log import logger
from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.conversation_repository import ConversationRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.access_policy import (
    AccessPolicyContextV1,
    AccessPolicyError,
    internal_access_policy,
)
from app.services.business_rule_service import BusinessRuleService
from app.services.conversation_context_service import resolve_conversation_context
from app.services.feedback_learning_service import FeedbackLearningService
from app.services.query_set_match_service import QuerySetMatchService
from app.services.query_trace_service import QueryTraceRecorder
from app.services.recommendation_service import RecommendationService
from app.services.semantic_term_service import SemanticTermService

_PUBLIC_CONTEXT_FIELDS = frozenset(
    {
        "type",
        "analysis_plan",
        "query_plan",
        "planning_roles",
        "selected_semantics",
        "decomposed_query",
        "query_plan_refined",
        "dry_plan_status",
        "dry_plan_checks",
        "sql_validation_stages",
        "explain_estimate",
        "explain_budget",
        "execution_mode",
        "execution_timeout_seconds",
        "tables",
        "relationships",
        "warnings",
        "build_id",
        "generation_mode",
        "generation_source",
        "query_dsl",
        "dsl_fallback_reason",
        "dsl_attempts",
        "sql_correction_attempts",
        "llm_calls",
        "query_set_id",
        "query_set_version",
        "query_set_hash",
        "semantic_release_id",
        "semantic_release_version",
        "business_rule_set_id",
        "business_rule_set_version",
        "semantic_term_matches",
        "verified_query_match",
        "verified_query_examples",
        "verified_exact_error",
        "business_rule_matches",
    }
)


class QueryService:
    """Run one graph execution and expose it as SSE or a synchronous result."""

    def __init__(
        self,
        dw_mysql_repository: DWMySQlRepository,
        meta_mysql_repository: MetaMySQlRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueInfoRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        query_trace_repository: QueryTraceRepository,
        feedback_learning_service: FeedbackLearningService | None = None,
        query_set_match_service: QuerySetMatchService | None = None,
        business_rule_service: BusinessRuleService | None = None,
        semantic_term_service: SemanticTermService | None = None,
        recommendation_service: RecommendationService | None = None,
        graph_runner=graph,
    ):
        self.dw_mysql_repository = dw_mysql_repository
        self.meta_mysql_repository = meta_mysql_repository
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository
        self.embedding_client = embedding_client
        self.query_trace_repository = query_trace_repository
        self.feedback_learning_service = feedback_learning_service
        self.query_set_match_service = query_set_match_service
        self.business_rule_service = business_rule_service
        self.semantic_term_service = semantic_term_service
        self.recommendation_service = recommendation_service or RecommendationService()
        self.graph_runner = graph_runner

    async def query(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        user_id: str | None = None,
        access_policy: AccessPolicyContextV1 | None = None,
    ) -> AsyncIterator[str]:
        """Serialize normalized events as standards-compliant Server-Sent Events."""

        async for event in self.events(
            query,
            parameters=parameters,
            conversation_id=conversation_id,
            parent_trace_id=parent_trace_id,
            regenerate_of_trace_id=regenerate_of_trace_id,
            user_id=user_id,
            access_policy=access_policy,
        ):
            yield _to_sse(event)

    async def query_sync(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        user_id: str | None = None,
        access_policy: AccessPolicyContextV1 | None = None,
    ) -> dict[str, Any]:
        """Collect the same normalized events used by SSE into one response body."""

        result: dict[str, Any] = {
            "request_id": "",
            "status": "failed",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "metrics": [],
            "time_range": "未限定",
            "explanation": None,
            "duration_ms": 0,
            "error": None,
            "generation_mode": "legacy",
            "generation_source": None,
            "query_dsl": None,
            "dsl_fallback_reason": None,
            "dsl_attempts": 0,
            "sql_correction_attempts": 0,
            "llm_calls": 0,
            "token_usage": None,
            "policy_version": None,
            "policy_hash": None,
            "policy_admin_bypass": False,
            "policy_domain": None,
            "policy_datasource": None,
            "build_id": None,
            "query_set_id": None,
            "query_set_version": None,
            "query_set_hash": None,
            "verified_query_match": None,
            "verified_query_examples": [],
            "verified_exact_error": None,
            "business_rule_matches": [],
            "semantic_term_matches": [],
            "semantic_release_id": None,
            "semantic_release_version": None,
            "business_rule_set_id": None,
            "business_rule_set_version": None,
            "conversation_id": conversation_id,
            "parent_trace_id": parent_trace_id,
            "regenerate_of_trace_id": regenerate_of_trace_id,
            "standalone_question": query,
            "context_inherited": False,
            "context_turns_used": [],
            "context_resolution_confidence": "high",
            "context_ambiguity_reason": None,
            "clarification": None,
            "recommendations": [],
            "recommendation_source": None,
            "query_plan": None,
            "planning_roles": [],
            "selected_semantics": None,
            "decomposed_query": [],
            "query_plan_refined": False,
            "dry_plan_status": None,
            "dry_plan_checks": [],
            "sql_validation_stages": [],
            "explain_estimate": None,
            "explain_budget": None,
            "execution_mode": None,
            "execution_timeout_seconds": None,
            "chart_spec": None,
        }
        async for event in self.events(
            query,
            parameters=parameters,
            conversation_id=conversation_id,
            parent_trace_id=parent_trace_id,
            regenerate_of_trace_id=regenerate_of_trace_id,
            user_id=user_id,
            access_policy=access_policy,
        ):
            result["request_id"] = event["request_id"]
            event_type = event["type"]
            if event_type == "context":
                for key in (
                    "generation_mode",
                    "generation_source",
                    "query_dsl",
                    "dsl_fallback_reason",
                    "dsl_attempts",
                    "sql_correction_attempts",
                    "llm_calls",
                    "token_usage",
                    "policy_version",
                    "policy_hash",
                    "policy_admin_bypass",
                    "policy_domain",
                    "policy_datasource",
                    "build_id",
                    "query_set_id",
                    "query_set_version",
                    "query_set_hash",
                    "verified_query_match",
                    "verified_query_examples",
                    "verified_exact_error",
                    "business_rule_matches",
                    "semantic_term_matches",
                    "semantic_release_id",
                    "semantic_release_version",
                    "business_rule_set_id",
                    "business_rule_set_version",
                    "conversation_id",
                    "parent_trace_id",
                    "regenerate_of_trace_id",
                    "standalone_question",
                    "context_inherited",
                    "context_turns_used",
                    "context_resolution_confidence",
                    "context_ambiguity_reason",
                    "query_plan",
                    "planning_roles",
                    "selected_semantics",
                    "decomposed_query",
                    "query_plan_refined",
                    "dry_plan_status",
                    "dry_plan_checks",
                    "sql_validation_stages",
                    "explain_estimate",
                    "explain_budget",
                    "execution_mode",
                    "execution_timeout_seconds",
                ):
                    if key in event and event[key] is not None:
                        result[key] = event[key]
            elif event_type == "sql":
                result["sql"] = event["sql"]
            elif event_type == "result":
                result["sql"] = event.get("sql") or result["sql"]
                result["columns"] = event.get("columns", [])
                result["rows"] = event.get("data", [])
                result["row_count"] = event.get("row_count", 0)
                result["truncated"] = event.get("truncated", False)
            elif event_type == "answer":
                result["metrics"] = event.get("metrics", [])
                result["time_range"] = event.get("time_range", "未限定")
                result["explanation"] = event.get("summary")
            elif event_type == "visualization":
                result["chart_spec"] = event.get("chart_spec")
            elif event_type == "clarification":
                result["clarification"] = event.get("message")
            elif event_type == "recommendations":
                result["recommendations"] = event.get("questions", [])
                result["recommendation_source"] = event.get("source")
                result["llm_calls"] = max(
                    result["llm_calls"],
                    int(event.get("llm_calls", result["llm_calls"])),
                )
            elif event_type == "error":
                result["error"] = event.get("message", "查询失败")
            elif event_type == "done":
                result["status"] = event["status"]
                result["duration_ms"] = event["duration_ms"]
                result["error"] = event.get("error") or result["error"]
                result["llm_calls"] = max(
                    result["llm_calls"], int(event.get("llm_calls") or 0)
                )
                result["token_usage"] = event.get("token_usage") or result["token_usage"]
        return result

    async def events(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        user_id: str | None = None,
        access_policy: AccessPolicyContextV1 | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield the fixed event protocol and always finish with a done event.

        API users must provide a resolved access policy. Trusted internal callers
        without a user id receive an explicit and auditable system bypass.
        """

        request_id = _ensure_request_id()
        await self.validate_conversation_context(
            user_id=user_id,
            conversation_id=conversation_id,
            parent_trace_id=parent_trace_id,
            regenerate_of_trace_id=regenerate_of_trace_id,
        )
        if access_policy is None:
            if user_id is not None:
                raise AccessPolicyError("查询缺少访问策略上下文")
            access_policy = internal_access_policy(
                domain="audio", datasource=app_config.db_dw.database
            )
        resolution = await resolve_conversation_context(
            query=query,
            conversation_id=conversation_id,
            parent_trace_id=parent_trace_id,
            regenerate_of_trace_id=regenerate_of_trace_id,
            user_id=user_id,
            repository=self.query_trace_repository,
            row_level_scope=access_policy.row_level_scope(),
            dialect=app_config.db_dw.dialect,
        )
        standalone_question = resolution.standalone_question
        recorder = QueryTraceRecorder(
            self.query_trace_repository,
            request_id,
            query,
            user_id=user_id,
            access_policy=access_policy,
            conversation_id=conversation_id,
            parent_trace_id=parent_trace_id,
            regenerate_of_trace_id=regenerate_of_trace_id,
            standalone_question=standalone_question,
        )
        context = DataAgentContext(
            dw_mysql_repository=self.dw_mysql_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            meta_repository_lock=asyncio.Lock(),
            column_qdrant_repository=self.column_qdrant_repository,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            embedding_client=self.embedding_client,
            feedback_learning_service=self.feedback_learning_service,
            query_set_match_service=self.query_set_match_service,
            business_rule_service=self.business_rule_service,
        )
        started_at = time.perf_counter()
        error_message: str | None = None
        final_status = "completed"
        recommendation_plan: dict[str, Any] = {}
        recommendation_tables: list[str] = []
        recommendation_answer = ""
        observed_llm_calls = 0
        usage_callback = UsageMetadataCallbackHandler()
        await recorder.start()
        try:
            # 放在 try 内，确保客户端在首个事件后断开时仍会执行追踪收尾。
            yield {
                "type": "context",
                "request_id": request_id,
                "conversation_id": conversation_id,
                "parent_trace_id": parent_trace_id,
                "regenerate_of_trace_id": regenerate_of_trace_id,
                "standalone_question": standalone_question,
                "context_inherited": resolution.inherited,
                "context_turns_used": list(resolution.used_trace_ids),
                "context_resolution_confidence": resolution.confidence,
                "context_ambiguity_reason": resolution.ambiguity_reason,
                **access_policy.public_metadata(),
            }
            if resolution.confidence == "low":
                final_status = "needs_input"
                yield {
                    "type": "clarification",
                    "request_id": request_id,
                    "message": resolution.ambiguity_reason or "请补充查询条件后重试。",
                    "standalone_question": standalone_question,
                }
            else:
                semantic_terms = await _resolve_semantic_terms(
                    self.semantic_term_service,
                    standalone_question,
                    domain=access_policy.domain,
                    datasource=access_policy.datasource,
                )
                async for raw_event in self.graph_runner.astream(
                    input=DataAgentState(
                        query=standalone_question,
                        query_parameters=parameters or {},
                        semantic_terms=semantic_terms,
                        access_policy=access_policy.model_dump(mode="json"),
                        row_level_scope=access_policy.row_level_scope(),
                    ),
                    context=context,
                    stream_mode="custom",
                    config={"callbacks": [usage_callback]},
                ):
                    await recorder.observe(raw_event)
                    if raw_event.get("type") == "context":
                        recommendation_plan = (
                            raw_event.get("query_plan")
                            or raw_event.get("analysis_plan")
                            or recommendation_plan
                        )
                        recommendation_tables = list(
                            raw_event.get("tables") or recommendation_tables
                        )
                        observed_llm_calls = max(
                            observed_llm_calls,
                            int(raw_event.get("llm_calls") or 0),
                        )
                    elif raw_event.get("type") == "answer":
                        recommendation_answer = str(raw_event.get("summary") or "")
                    # trace_sql 只用于审计落库，避免把未经校验的 SQL 暴露到公开 SSE 协议。
                    if raw_event.get("type") == "trace_sql":
                        continue
                    event = {**_public_graph_event(raw_event), "request_id": request_id}
                    if event.get("type") == "error":
                        error_message = str(event.get("message") or "查询失败")
                    yield event
                if not error_message and recommendation_answer:
                    try:
                        recommendation = await self.recommendation_service.recommend(
                            question=standalone_question,
                            query_plan=recommendation_plan,
                            answer_summary=recommendation_answer,
                            current_tables=recommendation_tables,
                            access_policy=access_policy,
                            usage_callback=usage_callback,
                        )
                        observed_llm_calls += recommendation.llm_calls
                        yield {
                            "type": "recommendations",
                            "request_id": request_id,
                            "questions": list(recommendation.questions),
                            "source": recommendation.source,
                            "llm_calls": observed_llm_calls,
                        }
                    except Exception:
                        logger.warning("追问推荐生成失败，主查询结果保持成功")
        except asyncio.CancelledError:
            final_status = "cancelled"
            error_message = "查询已取消或客户端连接已断开"
            raise
        except GeneratorExit:
            final_status = "cancelled"
            error_message = "查询已取消或客户端连接已断开"
            raise
        except Exception as exc:
            final_status = "failed"
            error_message = str(exc) or "查询执行失败"
            logger.exception("查询执行失败，追踪编号：{}", request_id)
            yield {
                "type": "error",
                "request_id": request_id,
                "stage": getattr(exc, "stage", "execution"),
                "reason": getattr(exc, "reason", "pipeline_failure"),
                "message": error_message,
            }
        finally:
            if final_status == "completed" and error_message:
                final_status = "failed"
            await recorder.finish(error_message, status=final_status)

        yield {
            "type": "done",
            "request_id": request_id,
            "status": final_status,
            "duration_ms": _elapsed_ms(started_at),
            "error": error_message,
            "llm_calls": observed_llm_calls,
            "token_usage": _summarize_token_usage(usage_callback),
        }

    async def validate_conversation_context(
        self,
        *,
        user_id: str | None,
        conversation_id: str | None,
        parent_trace_id: str | None,
        regenerate_of_trace_id: str | None = None,
    ) -> None:
        if conversation_id is None:
            if parent_trace_id is not None or regenerate_of_trace_id is not None:
                raise ConversationContextError("父 Trace 或重生成必须指定会话")
            return
        if user_id is None:
            raise ConversationContextError("会话查询必须包含用户身份")
        conversations = ConversationRepository(self.query_trace_repository.session)
        conversation = await conversations.get_for_user(conversation_id, user_id)
        if conversation is None:
            raise ConversationContextError("会话不存在或无权访问")
        if conversation.status != "active":
            raise ConversationContextError("已归档会话不能继续查询")
        for trace_id, label in (
            (parent_trace_id, "父 Trace"),
            (regenerate_of_trace_id, "重生成 Trace"),
        ):
            if trace_id is None:
                continue
            trace = await self.query_trace_repository.get_for_user(trace_id, user_id)
            if trace is None or trace.conversation_id != conversation_id:
                raise ConversationContextError(f"{label} 不存在或不属于当前会话")
        await conversations.touch(conversation)


class ConversationContextError(ValueError):
    pass


def _ensure_request_id() -> str:
    request_id = get_request_id()
    if request_id == 1:
        request_id = str(uuid.uuid4())
        set_request_id(request_id)
    return str(request_id)


def _to_sse(event: dict[str, Any]) -> str:
    event_name = event["type"]
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n"


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _public_graph_event(event: dict[str, Any]) -> dict[str, Any]:
    """Allowlist inspectable context fields; prompts, secrets and result rows stay private."""

    if event.get("type") != "context":
        return event
    return {key: value for key, value in event.items() if key in _PUBLIC_CONTEXT_FIELDS}


async def _resolve_semantic_terms(
    service: SemanticTermService | None,
    query: str,
    *,
    domain: str,
    datasource: str,
) -> list[dict[str, Any]]:
    if service is None:
        return []
    exact_result, vector_result = await asyncio.gather(
        service.exact_match(query, domain=domain, datasource=datasource),
        service.vector_search(query, domain=domain, datasource=datasource),
        return_exceptions=True,
    )
    matches = []
    for result in (exact_result, vector_result):
        if isinstance(result, BaseException):
            logger.warning("查询术语召回降级：{}", result)
            continue
        matches.extend(result)
    unique = {term.id: term for term in matches if term.status == "published"}
    return [
        {
            "id": term.id,
            "term_key": term.term_key,
            "standard_term": term.standard_term,
            "synonyms": list(term.synonyms),
            "bindings": [dict(binding) for binding in term.bindings],
            "version": term.version,
        }
        for term in list(unique.values())[:5]
    ]


def _summarize_token_usage(callback: UsageMetadataCallbackHandler) -> dict[str, int | bool]:
    records = list(callback.usage_metadata.values())
    return {
        "input_tokens": sum(int(item.get("input_tokens", 0)) for item in records),
        "output_tokens": sum(int(item.get("output_tokens", 0)) for item in records),
        "total_tokens": sum(int(item.get("total_tokens", 0)) for item in records),
        "available": bool(records),
    }
