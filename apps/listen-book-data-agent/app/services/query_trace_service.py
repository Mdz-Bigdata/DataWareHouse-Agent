"""Best-effort event recorder for graph custom-stream messages."""

from __future__ import annotations

import time

from app.core.log import logger
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.access_policy import AccessPolicyContextV1


class QueryTraceRecorder:
    def __init__(
        self,
        repository: QueryTraceRepository,
        trace_id: str,
        query: str,
        user_id: str | None = None,
        access_policy: AccessPolicyContextV1 | None = None,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        standalone_question: str | None = None,
    ):
        self.repository = repository
        self.trace_id = trace_id
        self.query = query
        self.user_id = user_id
        self.access_policy = access_policy
        self.conversation_id = conversation_id
        self.parent_trace_id = parent_trace_id
        self.regenerate_of_trace_id = regenerate_of_trace_id
        self.started_at = time.perf_counter()
        self.phase_started_at: dict[str, float] = {}
        self.phase_sql: dict[str, str] = {}
        self.phase_sequence = 0
        self.sql: str | None = None
        self.build_id: str | None = None
        self.error_message: str | None = None
        self.standalone_question: str = standalone_question or query
        self.query_plan_summary: dict | None = None
        self.answer_summary: str | None = None
        self.chart_spec: dict | None = None
        self.semantic_release_id: str | None = None
        self.semantic_release_version: int | None = None
        self.query_set_id: str | None = None
        self.query_set_version: int | None = None
        self.business_rule_set_id: str | None = None
        self.business_rule_set_version: int | None = None

    async def start(self) -> None:
        policy = self.access_policy
        await self._best_effort(
            self.repository.create_trace(
                self.trace_id,
                self.query,
                self.user_id,
                policy_version=policy.policy_version if policy else None,
                policy_hash=policy.policy_hash if policy else None,
                policy_admin_bypass=policy.admin_bypass if policy else False,
                conversation_id=self.conversation_id,
                parent_trace_id=self.parent_trace_id,
                regenerate_of_trace_id=self.regenerate_of_trace_id,
                standalone_question=self.standalone_question,
            )
        )

    async def observe(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            await self._observe_progress(event)
        elif event_type == "context":
            self.build_id = event.get("build_id") or self.build_id
            self.standalone_question = (
                event.get("standalone_question") or self.standalone_question
            )
            self.query_plan_summary = (
                event.get("query_plan")
                or event.get("analysis_plan")
                or self.query_plan_summary
            )
            self.semantic_release_id = (
                event.get("semantic_release_id") or self.semantic_release_id
            )
            self.semantic_release_version = (
                event.get("semantic_release_version") or self.semantic_release_version
            )
            self.query_set_id = event.get("query_set_id") or self.query_set_id
            self.query_set_version = event.get("query_set_version") or self.query_set_version
            self.business_rule_set_id = (
                event.get("business_rule_set_id") or self.business_rule_set_id
            )
            self.business_rule_set_version = (
                event.get("business_rule_set_version") or self.business_rule_set_version
            )
        elif event_type == "answer":
            self.sql = event.get("sql") or self.sql
            self.answer_summary = event.get("summary") or self.answer_summary
        elif event_type == "visualization":
            self.chart_spec = event.get("chart_spec") or self.chart_spec
        elif event_type in {"trace_sql", "sql", "result"}:
            self.sql = event.get("sql") or self.sql
            if event_type == "trace_sql" and event.get("sql"):
                status = event.get("status")
                step = (
                    "修复SQL"
                    if status == "corrected"
                    else "编译DSL"
                    if status == "dsl_compiled"
                    else "生成SQL"
                )
                self.phase_sql[step] = str(event["sql"])
        elif event_type == "error":
            self.error_message = str(event.get("message") or "查询失败")

    async def finish(
        self,
        error_message: str | None = None,
        *,
        status: str | None = None,
    ) -> None:
        error = error_message or self.error_message
        await self._best_effort(
            self.repository.finish_trace(
                trace_id=self.trace_id,
                status=status or ("failed" if error else "completed"),
                total_duration_ms=_elapsed_ms(self.started_at),
                sql=self.sql,
                build_id=self.build_id,
                error_message=error,
                standalone_question=self.standalone_question,
                query_plan_summary=self.query_plan_summary,
                answer_summary=self.answer_summary,
                chart_spec=self.chart_spec,
                semantic_release_id=self.semantic_release_id,
                semantic_release_version=self.semantic_release_version,
                query_set_id=self.query_set_id,
                query_set_version=self.query_set_version,
                business_rule_set_id=self.business_rule_set_id,
                business_rule_set_version=self.business_rule_set_version,
            )
        )

    async def _observe_progress(self, event: dict) -> None:
        step = str(event.get("step") or "未知阶段")
        status = str(event.get("status") or "unknown")
        if status == "running":
            self.phase_started_at[step] = time.perf_counter()
            return
        started_at = self.phase_started_at.pop(step, time.perf_counter())
        self.phase_sequence += 1
        raw_error = event.get("message") if status == "error" else None
        error_message = str(raw_error) if raw_error else None
        event_duration = event.get("duration_ms")
        duration_ms = (
            max(1, int(event_duration))
            if isinstance(event_duration, int | float)
            else _elapsed_ms(started_at)
        )
        phase_sql = event.get("sql") or self.phase_sql.pop(step, None)
        await self._best_effort(
            self.repository.record_phase(
                trace_id=self.trace_id,
                sequence=self.phase_sequence,
                step=step,
                status=status,
                duration_ms=duration_ms,
                error_message=error_message,
                sql=str(phase_sql) if phase_sql else None,
            )
        )

    async def _best_effort(self, operation) -> None:
        try:
            await operation
        except Exception:
            try:
                await self.repository.session.rollback()
            except Exception:
                logger.exception("查询追踪回滚失败")
            logger.exception("查询追踪写入失败，已跳过，不影响当前查询")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
