"""双路查询选路：内表物化 vs 外部表即席查 vs 向量索引。

来源 [S1-全景] 第七章《数据用得上：双路查询与语义检索》，选路判断标准原文表格：

| 典型负载 | 选路 | 原因 |
|---|---|---|
| 监控大屏 / 固定报表（高频、毫秒级） | StarRocks 内表 | 物化后稳定可控，不受湖端 Compaction 影响 |
| 探索式分析 / 临时取数（灵活、秒级可接受） | Paimon 外部表直查 | 零搬运零冗余，永远查最新数据 |
| 语义检索 / 相似样本圈选 | 向量索引 + 标量过滤 | HNSW 建在外部表上，向量与标量同表同权限 |

本模块只负责「选哪条路 + 把表名限定到那条路上」。ADS 服务层的默认路是第一条
（11 张 ADS 表都是高频固定报表负载）；第三条属 vector 子系统，本模块只给出委派说明，
不实现向量检索——[S1-05] 与本子系统的边界即在此。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from ..config import settings
from .constants import (
    HNSW_EF_CONSTRUCTION,
    HNSW_M,
    HNSW_METRIC,
    VECTOR_SEARCH_P95_LATENCY_SECONDS,
)
from .errors import ServiceUnavailableError

__all__ = [
    "WorkloadKind",
    "QueryRoute",
    "RouteDecision",
    "ROUTING_TABLE",
    "route_for",
    "qualify",
]


class WorkloadKind(str, Enum):
    """三类典型负载（[S1-全景] 第七章选路表第一列）。"""

    #: 监控大屏 / 固定报表（高频、毫秒级）
    DASHBOARD_REPORT = "dashboard_report"
    #: 探索式分析 / 临时取数（灵活、秒级可接受）
    EXPLORATORY_ADHOC = "exploratory_adhoc"
    #: 语义检索 / 相似样本圈选
    SEMANTIC_RETRIEVAL = "semantic_retrieval"


class QueryRoute(str, Enum):
    """三条查询路径（[S1-全景] 第七章选路表第二列）。"""

    STARROCKS_INTERNAL = "starrocks_internal"
    PAIMON_EXTERNAL = "paimon_external"
    VECTOR_INDEX = "vector_index"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """一条选路结论：走哪条路、原文给的理由、延迟预期。"""

    workload: WorkloadKind
    route: QueryRoute
    workload_cn: str
    reason_cn: str
    latency_expectation_cn: str


ROUTING_TABLE: Final[dict[WorkloadKind, RouteDecision]] = {
    WorkloadKind.DASHBOARD_REPORT: RouteDecision(
        workload=WorkloadKind.DASHBOARD_REPORT,
        route=QueryRoute.STARROCKS_INTERNAL,
        workload_cn="监控大屏 / 固定报表（高频、毫秒级）",
        reason_cn="物化后稳定可控，不受湖端 Compaction 影响",
        latency_expectation_cn="毫秒级",
    ),
    WorkloadKind.EXPLORATORY_ADHOC: RouteDecision(
        workload=WorkloadKind.EXPLORATORY_ADHOC,
        route=QueryRoute.PAIMON_EXTERNAL,
        workload_cn="探索式分析 / 临时取数（灵活、秒级可接受）",
        reason_cn="零搬运零冗余，永远查最新数据",
        latency_expectation_cn="秒级可接受",
    ),
    WorkloadKind.SEMANTIC_RETRIEVAL: RouteDecision(
        workload=WorkloadKind.SEMANTIC_RETRIEVAL,
        route=QueryRoute.VECTOR_INDEX,
        workload_cn="语义检索 / 相似样本圈选",
        reason_cn="HNSW 建在外部表上，向量与标量同表同权限",
        latency_expectation_cn=f"P95 延迟 ≤{VECTOR_SEARCH_P95_LATENCY_SECONDS:g}s",
    ),
}


def route_for(workload: WorkloadKind) -> RouteDecision:
    """按负载类型选路。

    Args:
        workload: 负载类型。

    Returns:
        选路结论（含原文给出的理由，便于在审计日志里解释「为什么走这条」）。

    Raises:
        ServiceUnavailableError: 语义检索路由不属于本子系统——向量索引
            （HNSW，M={M}、efConstruction={EFC}、{METRIC} 距离，[S1-全景] 第七章）
            由 vector 子系统实现，ADS 服务层只做标量路径。
    """
    decision = ROUTING_TABLE[workload]
    if decision.route is QueryRoute.VECTOR_INDEX:
        raise ServiceUnavailableError(
            "语义检索 / 相似样本圈选走向量索引路径，不在 ADS 服务层实现："
            f"HNSW 索引建在 StarRocks 外部表上（M={HNSW_M}、"
            f"efConstruction={HNSW_EF_CONSTRUCTION}、{HNSW_METRIC} 距离，"
            f"验收线 P95 ≤{VECTOR_SEARCH_P95_LATENCY_SECONDS:g}s）。"
            "请注入 vector 子系统的检索端口（见 services.SceneSearchCurationService 的 "
            "semantic_port 参数）后再调用"
        )
    return decision


# route_for 的 docstring 需要在类型检查前完成格式化，放在定义之后做一次替换，
# 避免在 docstring 里硬编码参数值造成两处数字不一致。
if route_for.__doc__:  # pragma: no cover - 纯文档处理
    route_for.__doc__ = route_for.__doc__.format(
        M=HNSW_M, EFC=HNSW_EF_CONSTRUCTION, METRIC=HNSW_METRIC
    )


def qualify(table: str, route: QueryRoute) -> str:
    """把表名限定到具体的查询路径上。

    Args:
        table: ADS 表名。
        route: 查询路径。

    Returns:
        可直接拼进 SQL 的反引号限定名：
        内表 ``` `adas_ads`.`ads_xxx` ```，
        外部表 ``` `paimon_catalog`.`adas_lakehouse`.`ads_xxx` ```。

    Raises:
        ServiceUnavailableError: 向量路径不由本模块限定表名。
    """
    cfg = settings().starrocks
    if route is QueryRoute.STARROCKS_INTERNAL:
        return f"`{cfg.internal_database}`.`{table}`"
    if route is QueryRoute.PAIMON_EXTERNAL:
        return f"`{cfg.external_catalog}`.`{settings().paimon.database}`.`{table}`"
    raise ServiceUnavailableError("向量索引路径的表限定由 vector 子系统负责，ADS 服务层不处理")
