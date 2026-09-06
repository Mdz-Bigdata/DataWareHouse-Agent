from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.query_feedback_mysql import QueryFeedbackMySQL
from app.models.mysql.query_trace_mysql import QueryTraceMySQL, QueryTracePhaseMySQL
from app.models.mysql.user_mysql import UserMySQL


class QueryTraceRepository:
    """Persist query metadata while deliberately excluding returned data rows."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_trace(
        self,
        trace_id: str,
        query_text: str,
        user_id: str | None = None,
        *,
        policy_version: str | None = None,
        policy_hash: str | None = None,
        policy_admin_bypass: bool = False,
        conversation_id: str | None = None,
        parent_trace_id: str | None = None,
        regenerate_of_trace_id: str | None = None,
        standalone_question: str | None = None,
    ) -> None:
        self.session.add(
            QueryTraceMySQL(
                id=trace_id,
                query_text=query_text,
                status="running",
                user_id=user_id,
                policy_version=policy_version,
                policy_hash=policy_hash,
                policy_admin_bypass=policy_admin_bypass,
                conversation_id=conversation_id,
                parent_trace_id=parent_trace_id,
                regenerate_of_trace_id=regenerate_of_trace_id,
                standalone_question=standalone_question or query_text,
            )
        )
        await self.session.commit()

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[QueryTraceMySQL]:
        """按属主过滤的查询记录，最新在前。属主是唯一过滤条件，避免越权读取。"""
        result = await self.session.execute(
            select(QueryTraceMySQL)
            .where(QueryTraceMySQL.user_id == user_id)
            .order_by(QueryTraceMySQL.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_for_user(self, trace_id: str, user_id: str) -> QueryTraceMySQL | None:
        result = await self.session.execute(
            select(QueryTraceMySQL).where(
                QueryTraceMySQL.id == trace_id,
                QueryTraceMySQL.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_successful_ancestors_for_user(
        self,
        *,
        conversation_id: str,
        parent_trace_id: str | None,
        user_id: str,
        limit: int = 3,
    ) -> list[QueryTraceMySQL]:
        """Walk one owned branch and return at most three nearest successful turns."""

        ancestors: list[QueryTraceMySQL] = []
        next_trace_id = parent_trace_id
        visited: set[str] = set()
        # The hop cap prevents malformed legacy data from causing unbounded traversal.
        for _ in range(50):
            if next_trace_id is None or next_trace_id in visited or len(ancestors) >= limit:
                break
            visited.add(next_trace_id)
            trace = await self.get_for_user(next_trace_id, user_id)
            if trace is None or trace.conversation_id != conversation_id:
                break
            next_trace_id = trace.parent_trace_id
            if trace.status == "completed":
                ancestors.append(trace)
        return ancestors

    async def delete_for_user(self, user_id: str) -> int:
        """清空当前用户的查询记录（含阶段明细），返回删除条数。"""
        owned_trace_ids = select(QueryTraceMySQL.id).where(QueryTraceMySQL.user_id == user_id)
        await self.session.execute(
            delete(QueryFeedbackMySQL).where(QueryFeedbackMySQL.trace_id.in_(owned_trace_ids))
        )
        # query_trace_phase 无外键级联，先按属主的 trace_id 集合删阶段明细，避免残留孤儿行
        await self.session.execute(
            delete(QueryTracePhaseMySQL).where(
                QueryTracePhaseMySQL.trace_id.in_(owned_trace_ids)
            )
        )
        result = await self.session.execute(
            delete(QueryTraceMySQL).where(QueryTraceMySQL.user_id == user_id)
        )
        await self.session.commit()
        return result.rowcount or 0

    async def record_phase(
        self,
        *,
        trace_id: str,
        sequence: int,
        step: str,
        status: str,
        duration_ms: int,
        error_message: str | None = None,
        sql: str | None = None,
    ) -> None:
        self.session.add(
            QueryTracePhaseMySQL(
                trace_id=trace_id,
                sequence=sequence,
                step=step,
                status=status,
                duration_ms=duration_ms,
                sql=sql,
                error_message=error_message[:2000] if error_message else None,
            )
        )
        await self.session.commit()

    async def record_reference_sql(
        self,
        *,
        trace_id: str,
        sql: str,
        duration_ms: int,
    ) -> None:
        """Append an evaluator reference SQL phase to an existing query trace."""

        result = await self.session.execute(
            select(func.max(QueryTracePhaseMySQL.sequence)).where(
                QueryTracePhaseMySQL.trace_id == trace_id
            )
        )
        last_sequence = int(result.scalar_one() or 0)
        await self.record_phase(
            trace_id=trace_id,
            sequence=last_sequence + 1,
            step="标准答案SQL",
            status="success",
            duration_ms=max(1, duration_ms),
            sql=sql,
        )

    async def finish_trace(
        self,
        *,
        trace_id: str,
        status: str,
        total_duration_ms: int,
        sql: str | None = None,
        build_id: str | None = None,
        error_message: str | None = None,
        standalone_question: str | None = None,
        query_plan_summary: dict | None = None,
        answer_summary: str | None = None,
        chart_spec: dict | None = None,
        semantic_release_id: str | None = None,
        semantic_release_version: int | None = None,
        query_set_id: str | None = None,
        query_set_version: int | None = None,
        business_rule_set_id: str | None = None,
        business_rule_set_version: int | None = None,
    ) -> None:
        trace = await self.session.get(QueryTraceMySQL, trace_id)
        if trace is None:
            return
        trace.status = status
        trace.total_duration_ms = total_duration_ms
        trace.sql = sql
        trace.build_id = build_id
        trace.error_message = error_message[:4000] if error_message else None
        trace.standalone_question = standalone_question or trace.standalone_question
        trace.query_plan_summary = query_plan_summary
        trace.answer_summary = answer_summary[:4000] if answer_summary else None
        trace.chart_spec = chart_spec
        trace.semantic_release_id = semantic_release_id
        trace.semantic_release_version = semantic_release_version
        trace.query_set_id = query_set_id
        trace.query_set_version = query_set_version
        trace.business_rule_set_id = business_rule_set_id
        trace.business_rule_set_version = business_rule_set_version
        trace.completed_at = datetime.now()
        await self.session.commit()

    # ==================== Phase: 查询分析统计（admin 全局） ====================

    async def get_overview_stats(self, days: int = 7) -> dict:
        """统计概览：总数、成功数、失败数、平均耗时 + 每日趋势。"""

        since = datetime.now() - timedelta(days=days)
        # SQLAlchemy 2.0 用 case() 替代 MySQL 的 IF()，跨方言兼容
        completed_cond = case((QueryTraceMySQL.status == "completed", 1), else_=0)
        failed_cond = case((QueryTraceMySQL.status == "failed", 1), else_=0)
        # 总体统计
        result = await self.session.execute(
            select(
                func.count().label("total"),
                func.sum(completed_cond).label("completed"),
                func.sum(failed_cond).label("failed"),
                func.avg(QueryTraceMySQL.total_duration_ms).label("avg_ms"),
            ).where(QueryTraceMySQL.started_at >= since)
        )
        row = result.one()
        total = int(row.total or 0)
        completed = int(row.completed or 0)
        avg_ms = int(row.avg_ms) if row.avg_ms else None

        # 每日统计
        daily_result = await self.session.execute(
            select(
                func.date(QueryTraceMySQL.started_at).label("date"),
                func.count().label("total"),
                func.sum(completed_cond).label("completed"),
                func.sum(failed_cond).label("failed"),
            )
            .where(QueryTraceMySQL.started_at >= since)
            .group_by(func.date(QueryTraceMySQL.started_at))
            .order_by(text("date"))
        )
        daily_stats = []
        for d in daily_result.all():
            d_total = int(d.total or 0)
            d_completed = int(d.completed or 0)
            daily_stats.append(
                {
                    "date": str(d.date),
                    "total": d_total,
                    "completed": d_completed,
                    "failed": int(d.failed or 0),
                    "success_rate": round(d_completed / d_total * 100, 1) if d_total else 0,
                }
            )

        return {
            "total": total,
            "completed": completed,
            "failed": int(row.failed or 0),
            "success_rate": round(completed / total * 100, 1) if total else 0,
            "avg_duration_ms": avg_ms,
            "daily_stats": daily_stats,
        }

    async def get_failure_reasons(self, limit: int = 10) -> list[dict]:
        """失败原因分布（按截断后的错误消息分组）。"""

        # 用 SUBSTRING 截断错误消息前 100 字符做分组
        truncated = func.substring(QueryTraceMySQL.error_message, 1, 100)
        result = await self.session.execute(
            select(truncated.label("reason"), func.count().label("cnt"))
            .where(QueryTraceMySQL.status == "failed")
            .group_by(truncated)
            .order_by(text("cnt DESC"))
            .limit(limit)
        )
        return [{"reason": row.reason or "(空)", "count": int(row.cnt)} for row in result.all()]

    async def get_duration_buckets(self, days: int = 7) -> list[dict]:
        """耗时分布（按桶分组）。"""

        since = datetime.now() - timedelta(days=days)
        # SQLAlchemy 2.0 的 case() 构造耗时桶（跨方言兼容）
        bucket_expr = case(
            (QueryTraceMySQL.total_duration_ms < 5000, "<5s"),
            (QueryTraceMySQL.total_duration_ms < 30000, "5-30s"),
            (QueryTraceMySQL.total_duration_ms < 60000, "30-60s"),
            (QueryTraceMySQL.total_duration_ms < 120000, "60-120s"),
            else_=">120s",
        ).label("bucket")
        completed_cond = case((QueryTraceMySQL.status == "completed", 1), else_=0)
        result = await self.session.execute(
            select(
                bucket_expr,
                func.count().label("count"),
                func.sum(completed_cond).label("completed"),
                func.avg(QueryTraceMySQL.total_duration_ms).label("avg_ms"),
            )
            .where(QueryTraceMySQL.started_at >= since)
            .where(QueryTraceMySQL.total_duration_ms.is_not(None))
            .group_by(bucket_expr)
            .order_by(text("bucket"))
        )
        return [
            {
                "bucket": row.bucket,
                "count": int(row.count or 0),
                "completed": int(row.completed or 0),
                "avg_ms": int(row.avg_ms) if row.avg_ms else 0,
            }
            for row in result.all()
        ]

    async def get_phase_stats(self, days: int = 7) -> list[dict]:
        """阶段耗时排行。"""

        since = datetime.now() - timedelta(days=days)
        success_cond = case((QueryTracePhaseMySQL.status == "success", 1), else_=0)
        error_cond = case((QueryTracePhaseMySQL.status == "error", 1), else_=0)
        # query_trace_phase 没有 started_at，用 recorded_at 近似过滤
        result = await self.session.execute(
            select(
                QueryTracePhaseMySQL.step,
                func.avg(QueryTracePhaseMySQL.duration_ms).label("avg_ms"),
                func.sum(success_cond).label("success_count"),
                func.sum(error_cond).label("error_count"),
            )
            .where(QueryTracePhaseMySQL.recorded_at >= since)
            .group_by(QueryTracePhaseMySQL.step)
            .order_by(text("avg_ms DESC"))
        )
        return [
            {
                "step": row.step,
                "avg_ms": int(row.avg_ms) if row.avg_ms else 0,
                "success_count": int(row.success_count or 0),
                "error_count": int(row.error_count or 0),
            }
            for row in result.all()
        ]

    async def list_all_traces(
        self,
        limit: int = 100,
        status_filter: str | None = None,
    ) -> list[dict]:
        """admin 全局查询明细（跨用户，含 SQL 和用户名）。"""

        query = (
            select(
                QueryTraceMySQL.id,
                QueryTraceMySQL.user_id,
                UserMySQL.username.label("username"),
                QueryTraceMySQL.query_text,
                QueryTraceMySQL.status,
                QueryTraceMySQL.sql,
                QueryTraceMySQL.error_message,
                QueryTraceMySQL.total_duration_ms,
                QueryTraceMySQL.build_id,
                QueryTraceMySQL.started_at,
                QueryTraceMySQL.completed_at,
            )
            .outerjoin(UserMySQL, QueryTraceMySQL.user_id == UserMySQL.id)
            .order_by(QueryTraceMySQL.started_at.desc())
            .limit(limit)
        )
        if status_filter in ("completed", "failed"):
            query = query.where(QueryTraceMySQL.status == status_filter)
        result = await self.session.execute(query)
        traces = [dict(row._mapping) for row in result.all()]  # type: ignore[attr-defined]
        if not traces:
            return []

        phase_result = await self.session.execute(
            select(
                QueryTracePhaseMySQL.trace_id,
                QueryTracePhaseMySQL.sequence,
                QueryTracePhaseMySQL.step,
                QueryTracePhaseMySQL.status,
                QueryTracePhaseMySQL.duration_ms,
                QueryTracePhaseMySQL.sql,
                QueryTracePhaseMySQL.error_message,
            )
            .where(QueryTracePhaseMySQL.trace_id.in_([trace["id"] for trace in traces]))
            .order_by(QueryTracePhaseMySQL.trace_id, QueryTracePhaseMySQL.sequence)
        )
        phases_by_trace: dict[str, list[dict]] = {}
        for row in phase_result.all():
            phase = dict(row._mapping)  # type: ignore[attr-defined]
            phases_by_trace.setdefault(phase.pop("trace_id"), []).append(phase)
        for trace in traces:
            trace["phases"] = phases_by_trace.get(trace["id"], [])
        return traces
