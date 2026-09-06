"""Phase 查询分析后台的 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AnalyticsOverview(BaseModel):
    """统计概览卡片数据。"""

    total: int
    completed: int
    failed: int
    success_rate: float  # 百分比，0-100
    avg_duration_ms: int | None
    # 最近 7 天每日成功率（供前端画趋势用）
    daily_stats: list[DailyStat]


class DailyStat(BaseModel):
    date: str  # YYYY-MM-DD
    total: int
    completed: int
    failed: int
    success_rate: float


class FailureReasonItem(BaseModel):
    """失败原因分布条目。"""

    reason: str  # 截断后的错误原因
    count: int


class DurationBucket(BaseModel):
    """耗时分布桶。"""

    bucket: str  # 如 "<5s" "5-30s"
    count: int
    completed: int
    avg_ms: int


class PhaseStat(BaseModel):
    """阶段耗时统计。"""

    step: str
    avg_ms: int
    success_count: int
    error_count: int


class AnalyticsStats(BaseModel):
    """完整统计聚合（一次请求返回所有图表数据）。"""

    overview: AnalyticsOverview
    failure_reasons: list[FailureReasonItem]
    duration_buckets: list[DurationBucket]
    phase_stats: list[PhaseStat]


class TracePhaseItem(BaseModel):
    """一次查询中的阶段尝试，包含校验失败 SQL 和原因。"""

    sequence: int
    step: str
    status: str
    duration_ms: int
    sql: str | None
    error_message: str | None


class TraceDetailItem(BaseModel):
    """查询明细条目（含 SQL，admin 全局可见）。"""

    id: str
    user_id: str | None
    username: str | None  # 关联用户名（方便展示）
    query_text: str
    status: str
    sql: str | None
    error_message: str | None
    total_duration_ms: int | None
    build_id: str | None
    started_at: datetime
    completed_at: datetime | None
    phases: list[TracePhaseItem]
