"""统一 API 网关：认证、限流、审计。

[S1-全景] 第九章的服务层四层结构（原文原话）：

    业务平台（谁在调用）→ 统一 API 网关（认证、限流、审计）
    → 六项闭环业务服务 → 基础技术服务（元数据 / 权限 / 质量 / 告警）

本模块实现的是第二层。它不碰 SQL，也不碰业务语义，只做三件事：
  · 认证：令牌 → 调用方（哪个业务平台）；
  · 限流：按调用方令牌桶配额，防止一块大屏的轮询打垮所有人的查询；
  · 审计：谁在什么时候调了哪个接口、耗时多少、成功与否，全部落环形缓冲区。

⚠️ 原文只给了「认证、限流、审计」三个词，没给协议、配额、令牌格式。
本模块的实现方式（进程内路由 + 令牌桶 + 环形审计缓冲）是本项目设计：
它刻意做成**传输无关**的——没有绑定 HTTP 框架，Flask/FastAPI/gRPC 都可以在外面包一层，
调用 :meth:`ApiGateway.handle` 即可。
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    GATEWAY_AUDIT_RING_SIZE,
    GATEWAY_BURST_CAPACITY,
    GATEWAY_DEFAULT_QPS,
)
from .errors import (
    AdsError,
    AuthenticationError,
    AuthorizationError,
    RateLimitExceededError,
    RouteNotFoundError,
)
from .products import BusinessPlatform

__all__ = [
    "Handler",
    "Principal",
    "TokenAuthenticator",
    "TokenBucketRateLimiter",
    "AuditRecord",
    "AuditLog",
    "ApiRoute",
    "ApiGateway",
    "is_gateway_error",
    "error_status",
]

_LOG = logging.getLogger(__name__)

#: 路径参数占位符，如 ``production/batch/{batch_id}/progress``
_PATH_PARAM_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

Handler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class Principal:
    """一个调用方：哪个业务平台、以什么身份、能调哪些接口。

    Args:
        platform: 调用方平台（[S1-全景] 第二章的 9 大平台 + 监控大屏）。
        subject: 调用方标识（服务账号名），审计日志里记这个。
        scopes: 可访问的接口路径前缀集合；``{"*"}`` 表示全部。
        qps: 该调用方的 QPS 配额。
    """

    platform: BusinessPlatform
    subject: str
    scopes: frozenset[str] = frozenset({"*"})
    qps: int = GATEWAY_DEFAULT_QPS

    def allows(self, path: str) -> bool:
        if "*" in self.scopes:
            return True
        return any(path == s or path.startswith(s.rstrip("*")) for s in self.scopes)


class TokenAuthenticator:
    """最简令牌认证：令牌 → :class:`Principal`。

    ⚠️ 原文未明确，本项目设计：原文只说网关负责「认证」，没给认证协议。
    这里做成可替换的最小实现——生产环境应换成 OAuth2/JWT 校验，
    只要仍然返回一个 Principal，网关其余部分不用改。
    令牌不落日志（审计里只记 subject），避免凭证泄漏。
    """

    def __init__(self, tokens: Mapping[str, Principal] | None = None) -> None:
        self._tokens: dict[str, Principal] = dict(tokens or {})

    def register(self, token: str, principal: Principal) -> None:
        """登记一个调用方令牌。"""
        if not token:
            raise ValueError("令牌不能为空")
        self._tokens[token] = principal

    def authenticate(self, token: str | None) -> Principal:
        """校验令牌。

        Raises:
            AuthenticationError: 令牌缺失或未登记。
        """
        if not token:
            raise AuthenticationError("缺少调用令牌（请在请求头携带业务平台的服务账号令牌）")
        principal = self._tokens.get(token)
        if principal is None:
            raise AuthenticationError("令牌无效或已失效")
        return principal


class TokenBucketRateLimiter:
    """按调用方限流的令牌桶。

    ⚠️ 原文未明确，本项目设计：默认速率 :data:`constants.GATEWAY_DEFAULT_QPS` QPS、
    桶容量 :data:`constants.GATEWAY_BURST_CAPACITY`——大屏首屏会并发拉十几张表，
    容量必须大于速率才不至于一进页面就被限流。
    """

    def __init__(self, *, burst: int = GATEWAY_BURST_CAPACITY) -> None:
        self._burst = burst
        self._state: dict[str, tuple[float, float]] = {}  # subject -> (令牌数, 上次更新时刻)

    def acquire(self, subject: str, qps: int, *, cost: float = 1.0) -> None:
        """取一个令牌。

        Raises:
            RateLimitExceededError: 桶空了。
        """
        now = time.monotonic()
        tokens, last = self._state.get(subject, (float(self._burst), now))
        tokens = min(float(self._burst), tokens + (now - last) * qps)
        if tokens < cost:
            wait = (cost - tokens) / qps if qps > 0 else float("inf")
            self._state[subject] = (tokens, now)
            raise RateLimitExceededError(f"{subject} 超出 {qps} QPS 配额，请 {wait:.2f}s 后重试")
        self._state[subject] = (tokens - cost, now)

    def reset(self, subject: str | None = None) -> None:
        """清空限流状态（测试与运维用）。"""
        if subject is None:
            self._state.clear()
        else:
            self._state.pop(subject, None)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """一条审计记录。"""

    at: float
    subject: str
    platform: str
    path: str
    params: Mapping[str, Any]
    ok: bool
    elapsed_ms: float
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "subject": self.subject,
            "platform": self.platform,
            "path": self.path,
            "params": dict(self.params),
            "ok": self.ok,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
        }


class AuditLog:
    """环形审计缓冲 + 结构化日志。

    ⚠️ 原文未明确，本项目设计：原文只说网关要「审计」，没说落到哪。
    这里内存里留最近 :data:`constants.GATEWAY_AUDIT_RING_SIZE` 条供排障即时查看，
    同时每条都打 INFO 日志，由接入方的日志采集落库——服务层不假设任何存储。
    """

    def __init__(self, size: int = GATEWAY_AUDIT_RING_SIZE) -> None:
        self._records: deque[AuditRecord] = deque(maxlen=size)

    def record(self, record: AuditRecord) -> None:
        self._records.append(record)
        _LOG.info(
            "ads.api %s subject=%s platform=%s ok=%s elapsed=%.1fms %s",
            record.path,
            record.subject,
            record.platform,
            record.ok,
            record.elapsed_ms,
            record.error,
        )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近若干条审计记录（新的在前）。"""
        return [r.as_dict() for r in list(self._records)[-limit:][::-1]]

    def __len__(self) -> int:
        return len(self._records)


@dataclass(frozen=True, slots=True)
class ApiRoute:
    """一条接口路由：路径模板 → 处理函数。

    路径模板用 ``{name}`` 声明路径参数，例如原文的代表 API
    ``production/batch/{batch_id}/progress``。
    """

    path: str
    handler: Handler
    summary: str
    service_cn: str

    @property
    def pattern(self) -> re.Pattern[str]:
        escaped = re.escape(self.path)
        # re.escape 会把 { } 也转义，这里再换回参数捕获组
        pattern = re.sub(
            r"\\\{([a-z_][a-z0-9_]*)\\\}", lambda m: f"(?P<{m.group(1)}>[^/]+)", escaped
        )
        return re.compile(f"^{pattern}$")

    @property
    def path_params(self) -> tuple[str, ...]:
        return tuple(_PATH_PARAM_RE.findall(self.path))


@dataclass(slots=True)
class ApiGateway:
    """统一 API 网关：业务平台调用 ADS 能力的唯一入口。

    Args:
        authenticator: 认证器。
        limiter: 限流器。
        audit: 审计日志。

    Examples:
        路由注册与调用（传输层由使用方在外面包 HTTP/gRPC）::

            gw = ApiGateway()
            gw.authenticator.register("tok-dash", Principal(
                BusinessPlatform.MONITOR_DASHBOARD, "monitor-dashboard"))
            gw.route("closed-loop/overview", suite.production.closed_loop_overview,
                     "闭环大盘", "数据生产追踪")
            gw.handle("closed-loop/overview", token="tok-dash")
    """

    authenticator: TokenAuthenticator = field(default_factory=TokenAuthenticator)
    limiter: TokenBucketRateLimiter = field(default_factory=TokenBucketRateLimiter)
    audit: AuditLog = field(default_factory=AuditLog)
    _routes: list[ApiRoute] = field(default_factory=list, repr=False)

    # ---- 注册 ----

    def route(self, path: str, handler: Handler, summary: str, service_cn: str) -> ApiRoute:
        """注册一条接口。同名路径重复注册会被拒绝，避免静默覆盖。"""
        path = path.strip("/")
        if any(r.path == path for r in self._routes):
            raise ValueError(f"接口路径重复注册: {path}")
        api = ApiRoute(path, handler, summary, service_cn)
        self._routes.append(api)
        return api

    def routes(self) -> list[dict[str, str]]:
        """已注册的接口清单，供业务平台自助发现。"""
        return [
            {"path": r.path, "summary": r.summary, "service": r.service_cn} for r in self._routes
        ]

    def _match(self, path: str) -> tuple[ApiRoute, dict[str, str]]:
        path = path.strip("/")
        for route in self._routes:
            m = route.pattern.match(path)
            if m:
                return route, m.groupdict()
        raise RouteNotFoundError(
            f"未注册的接口路径: {path!r}；已注册 {len(self._routes)} 条，可调 routes() 查看"
        )

    # ---- 调用 ----

    def handle(
        self,
        path: str,
        *,
        token: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """处理一次调用：认证 → 鉴权 → 限流 → 分发 → 审计。

        Args:
            path: 接口路径，可含路径参数的具体取值，如 ``production/batch/B1/progress``。
            token: 调用方令牌。
            params: 查询参数，与路径参数合并后传给处理函数。

        Returns:
            处理函数的返回值（一般是 dict 或 list[dict]，可直接序列化成 JSON）。

        Raises:
            AuthenticationError / AuthorizationError / RateLimitExceededError /
            RouteNotFoundError: 网关层拒绝。
            AdsError: 业务服务层抛出的错误按原样上抛（并已计入审计）。
        """
        started = time.perf_counter()
        merged: dict[str, Any] = dict(params or {})
        principal: Principal | None = None
        route: ApiRoute | None = None
        try:
            principal = self.authenticator.authenticate(token)
            route, path_params = self._match(path)
            if not principal.allows(route.path):
                raise AuthorizationError(
                    f"{principal.subject}（{principal.platform.name_cn}）"
                    f"没有 {route.path} 的访问范围"
                )
            self.limiter.acquire(principal.subject, principal.qps)
            merged.update(path_params)
            result = route.handler(**merged)
        except Exception as exc:
            self._audit(principal, route, path, merged, started, exc)
            raise
        self._audit(principal, route, path, merged, started, None)
        return result

    def _audit(
        self,
        principal: Principal | None,
        route: ApiRoute | None,
        path: str,
        params: Mapping[str, Any],
        started: float,
        error: Exception | None,
    ) -> None:
        self.audit.record(
            AuditRecord(
                at=time.time(),
                subject=principal.subject if principal else "anonymous",
                platform=principal.platform.value if principal else "unknown",
                path=route.path if route else path,
                # 审计里只留参数键值，令牌永远不进审计
                params={k: v for k, v in params.items() if k != "token"},
                ok=error is None,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                error=f"{type(error).__name__}: {error}" if error else "",
            )
        )

    # ---- 批量注册 ----

    def register_all(self, routes: Iterable[tuple[str, Handler, str, str]]) -> int:
        """批量注册接口，返回注册条数。"""
        count = 0
        for path, handler, summary, service_cn in routes:
            self.route(path, handler, summary, service_cn)
            count += 1
        return count


def is_gateway_error(exc: BaseException) -> bool:
    """判断一个异常是否属于网关层拒绝（便于上层映射 HTTP 4xx）。

    **这是供外部传输层调用的公开 API**，本模块内部不调用它：
    :meth:`ApiGateway.handle` 按原样上抛异常，是否区分「网关拒绝」与
    「业务服务报错」由包在外面的那层（Flask / FastAPI / gRPC）决定——
    本网关刻意做成传输无关的，不自己产出状态码。
    """
    return isinstance(
        exc, (AuthenticationError, AuthorizationError, RateLimitExceededError, RouteNotFoundError)
    )


def error_status(exc: BaseException) -> int:
    """把异常映射成 HTTP 状态码，供使用方的传输层复用。

    与 :func:`is_gateway_error` 一样是**供外部传输层调用的公开 API**：
    ``handle()`` 抛什么就是什么，状态码由接入方在自己的框架里贴。

    ⚠️ 原文未明确，本项目设计：原文没规定错误码体系，这里给一套常识映射——
    401 认证 / 403 鉴权 / 429 限流 / 404 路由 / 503 依赖不可用 /
    400 其余 ADS 错误（调用方参数问题）/ 500 非 ADS 异常。
    """
    if isinstance(exc, AuthenticationError):
        return 401
    if isinstance(exc, AuthorizationError):
        return 403
    if isinstance(exc, RateLimitExceededError):
        return 429
    if isinstance(exc, RouteNotFoundError):
        return 404
    if isinstance(exc, AdsError):
        from .errors import BackendUnavailableError

        return 503 if isinstance(exc, BackendUnavailableError) else 400
    return 500
