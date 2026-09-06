"""查询分析后台 API（admin 专属）。

提供查询统计聚合与全局明细，供前端 AdminAnalyticsPage 展示。
所有端点要求 admin 权限，跨用户可见。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.agent.dependencies import get_query_trace_repository
from app.api.deps import require_admin
from app.api.schemas.analytics_schema import (
    AnalyticsStats,
    TraceDetailItem,
)
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.query_trace_repository import QueryTraceRepository

admin_analytics_router = APIRouter(tags=["查询分析"])


@admin_analytics_router.get("/api/admin/analytics/stats", response_model=AnalyticsStats)
async def get_analytics_stats(
    _: Annotated[UserMySQL, Depends(require_admin)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
):
    """查询统计聚合：概览 + 失败分布 + 耗时分布 + 阶段排行。"""

    overview = await query_trace_repository.get_overview_stats(days=days)
    failure_reasons = await query_trace_repository.get_failure_reasons(limit=10)
    duration_buckets = await query_trace_repository.get_duration_buckets(days=days)
    phase_stats = await query_trace_repository.get_phase_stats(days=days)
    return AnalyticsStats(
        overview=overview,  # type: ignore[arg-type]  dict 结构匹配 AnalyticsOverview
        failure_reasons=failure_reasons,
        duration_buckets=duration_buckets,
        phase_stats=phase_stats,
    )


@admin_analytics_router.get("/api/admin/analytics/traces", response_model=list[TraceDetailItem])
async def list_all_traces(
    _: Annotated[UserMySQL, Depends(require_admin)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    status: Annotated[str | None, Query(pattern="^(completed|failed)$")] = None,
):
    """全局查询明细（admin 跨用户，含 SQL）。支持按状态筛选。"""

    rows = await query_trace_repository.list_all_traces(limit=limit, status_filter=status)
    return [
        TraceDetailItem(
            id=row["id"],
            user_id=row["user_id"],
            username=row["username"],
            query_text=row["query_text"],
            status=row["status"],
            sql=row["sql"],
            error_message=row["error_message"],
            total_duration_ms=row["total_duration_ms"],
            build_id=row["build_id"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            phases=row["phases"],
        )
        for row in rows
    ]
