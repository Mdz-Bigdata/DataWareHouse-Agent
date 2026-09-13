"""Dependency-readiness aggregator used by the HTTP health routes.

每个依赖有三种状态，而不是二元的 ok/error：

* ``ok``              —— 依赖已启用且连通。
* ``not_configured``  —— 依赖**按设计未启用**（当前 profile 根本不包含它）。
  这种不是故障，不拉低整体状态。
* ``error``           —— 依赖应该在却连不上，这才是真故障，整体转 ``unavailable``。

判定 ``not_configured`` 必须有**明确依据**，只有下面三条，缺一不可地保守：

1. 显式开关关掉（``enabled=False``）—— 运维明说了「这个 profile 不带它」。
2. 地址为空（``host`` 空字符串）—— 压根没配地址，且该依赖被标记为可选。
3. 该依赖被标记为 ``optional`` 且处于 auto 档（``enabled is None``）且主机名
   **不可解析**（DNS NXDOMAIN）。Compose 里不在当前 profile 的服务没有 DNS
   记录，这正是「按设计未启用」的机器可证据据。

反过来，下面几种绝不会被吞成 ``not_configured``：

* ``enabled=True``（显式启用）时，任何失败——包括 DNS 解析不了——都是 ``error``。
* 非 optional 的依赖（元数据库/业务库/Qdrant/ES）任何失败都是 ``error``。
* 主机名能解析但连不上/超时/返回异常，一律 ``error``；只有「解析不出来」才算缺席。

``soft_fail`` 是给 Redis 这类纯加速层留的：失败只报 ``degraded``，永远不参与
整体 ready 判定（缓存挂了要降级直查，不该让整个服务 503）。
"""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

STATUS_OK = "ok"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_ERROR = "error"
STATUS_DEGRADED = "degraded"

DEFAULT_PROBE_TIMEOUT = 3.0
DEFAULT_RESOLVE_TIMEOUT = 2.0

# detail 只允许固定 token，避免异常信息把凭据带进健康响应里。
_SAFE_DETAIL = re.compile(r"^[a-z0-9_]{1,64}$")


class DependencyUnavailable(RuntimeError):
    """探针主动上报的不可用，附带一个固定的原因 token（非自由文本）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Dependency:
    """一个依赖的探测定义 + 它「该不该在」的判定依据。"""

    name: str
    probe: Callable[[], Awaitable[None]]
    # 该依赖允许在某些 profile 里整体缺席（缺席不是故障）。
    optional: bool = False
    # 显式开关：True=必须在（任何失败都是真故障）；False=显式关闭；None=auto。
    enabled: bool | None = None
    # 用于 DNS 判定与诊断展示；port 仅用于 getaddrinfo 与 target 文案。
    host: str = ""
    port: int = 0
    # 失败只降级、永不拉低整体 ready（Redis 这类加速层）。
    soft_fail: bool = False

    @property
    def target(self) -> str:
        if not self.host:
            return ""
        return f"{self.host}:{self.port}" if self.port else self.host


def tri_state(value: object) -> bool | None:
    """把开关解析成 True / False / None（auto）。无法识别的值一律当 auto。"""

    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


async def readiness_report(
    dependencies: Sequence[Dependency] | Mapping[str, Callable[[], Awaitable[None]]],
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT,
) -> dict:
    """并发探测所有依赖并汇总。

    兼容旧签名：传 ``{name: probe}`` 时每个依赖都按「必需且硬失败」处理，
    行为与改造前完全一致。
    """

    specs = _normalise(dependencies)
    results = await asyncio.gather(*(_evaluate(spec, timeout_seconds) for spec in specs))
    report = {name: result for name, result in results}
    # 只有 error 才算真故障；not_configured / degraded 都不拉低整体状态。
    ready = not any(result["status"] == STATUS_ERROR for result in report.values())
    return {
        "status": "ready" if ready else "unavailable",
        "dependencies": report,
        # 如实列出「按设计未启用」的依赖，供网关与人工一眼看到，而不是藏起来。
        "not_configured": sorted(
            name for name, result in report.items() if result["status"] == STATUS_NOT_CONFIGURED
        ),
        "degraded": sorted(
            name for name, result in report.items() if result["status"] == STATUS_DEGRADED
        ),
    }


def _normalise(
    dependencies: Sequence[Dependency] | Mapping[str, Callable[[], Awaitable[None]]],
) -> tuple[Dependency, ...]:
    if isinstance(dependencies, Mapping):
        return tuple(Dependency(name, probe) for name, probe in dependencies.items())
    return tuple(dependencies)


async def _evaluate(spec: Dependency, timeout_seconds: float) -> tuple[str, dict]:
    # 1. 显式关闭：按设计未启用，连探都不探。
    if spec.enabled is False:
        return spec.name, _state(spec, STATUS_NOT_CONFIGURED, "disabled_by_config")

    # 2. 没有地址。显式启用却没地址是配置错误，必须报 error。
    if not spec.host and (spec.optional or spec.enabled is True):
        if spec.enabled is True:
            return spec.name, _state(spec, STATUS_ERROR, "host_not_configured")
        return spec.name, _state(spec, STATUS_NOT_CONFIGURED, "host_not_configured")

    # 3. 可选 + auto 档：主机名解析不出来 = 该服务不在当前 profile 里。
    #    显式启用（enabled is True）和非可选依赖都不走这一条，故障不会被吞。
    if spec.optional and spec.enabled is None and spec.host:
        if await _definitely_unresolvable(spec.host, spec.port):
            return spec.name, _state(spec, STATUS_NOT_CONFIGURED, "dns_unresolved")

    # 4. 真探一次。到这里任何失败都是真故障（soft_fail 依赖降级为 degraded）。
    try:
        await asyncio.wait_for(spec.probe(), timeout=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - 探针异常类型不可穷举，一律视作故障
        status = STATUS_DEGRADED if spec.soft_fail else STATUS_ERROR
        return spec.name, _state(spec, status, _detail(exc))
    return spec.name, _state(spec, STATUS_OK, "")


async def _definitely_unresolvable(host: str, port: int) -> bool:
    """主机名是否**确定**解析不出来。

    只有拿到明确的解析失败（NXDOMAIN 之类的 ``socket.gaierror``）才返回 True。
    解析超时或其他异常一律返回 False —— 宁可继续探测报 error，也不把真故障
    误判成「未启用」。
    """

    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.getaddrinfo(host, port or None, type=socket.SOCK_STREAM),
            timeout=DEFAULT_RESOLVE_TIMEOUT,
        )
    except socket.gaierror:
        return True
    except Exception:  # noqa: BLE001 - 解析超时/其他异常按「可能存在」处理
        return False
    return False


def _state(spec: Dependency, status: str, detail: str) -> dict:
    state: dict[str, object] = {"status": status}
    if detail:
        state["detail"] = detail
    if spec.target:
        state["target"] = spec.target
    if spec.optional:
        state["optional"] = True
    return state


def _detail(exc: BaseException) -> str:
    """故障详情：固定 token 优先，否则退回异常类名（绝不回显异常消息）。"""

    if isinstance(exc, DependencyUnavailable) and _SAFE_DETAIL.match(exc.reason):
        return exc.reason
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    return type(exc).__name__
