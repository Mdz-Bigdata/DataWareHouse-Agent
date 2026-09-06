"""API 限流（Phase 0.5）。

基于 slowapi 实现。关键设计（规避已知 bug）：
- 使用装饰器 @limiter.limit() 模式，**不使用 SlowAPIMiddleware**。
  原因：SlowAPIMiddleware 在 StreamingResponse（如 /api/query SSE）上会
  重复发送 http.response.start 导致崩溃（slowapi issue #249/#260）。
- 必须配合 fastapi==0.128.0，勿升级至 >=0.137（slowapi issue #281，默认限流失效）。
- 限流 key 统一按客户端 IP。
  理由：slowapi 的 key_func 在 endpoint 执行前调用，此时鉴权尚未完成，
  无法可靠拿到用户名；强行通过 request.state 传递会增加脆弱性。
  按 IP 限流对登录防爆破和 LLM 成本保护均已足够（内网部署场景 IP 即用户代理）。

使用方式（在 router endpoint 上）：
    from app.core.rate_limit import limiter, rate_limit_query

    @query_router.post("/api/query")
    @limiter.limit(rate_limit_query())
    async def query_answer(request: Request, ...):
        ...

注意：slowapi 要求被装饰的 endpoint 必须包含 `request: Request` 形参。
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.conf.app_config import app_config

# 全局 limiter 实例。key_func 决定限流维度，这里统一按 IP。
limiter = Limiter(key_func=get_remote_address, enabled=app_config.rate_limit.enable)


def rate_limit_login() -> str:
    """登录端点限流规则。disable 时返回空串。"""

    return app_config.rate_limit.login if app_config.rate_limit.enable else ""


def rate_limit_query() -> str:
    """查询端点限流规则（保护 LLM 成本）。disable 时返回空串。"""

    return app_config.rate_limit.query if app_config.rate_limit.enable else ""
