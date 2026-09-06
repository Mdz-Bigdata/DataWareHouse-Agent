"""Prometheus 指标暴露端点（Phase 0.6）。

GET /metrics 返回 Prometheus 文本格式指标，供 Prometheus 抓取。
该端点不需要 JWT 鉴权（指标数据不含敏感业务信息），但建议在生产由 Nginx/网关层
限制内网访问，避免指标泄露给公网。此处遵循最小开放原则，不加业务鉴权。
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

metrics_router = APIRouter(tags=["监控"])


@metrics_router.get("/metrics")
async def metrics() -> Response:
    """Prometheus 抓取端点。"""

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
