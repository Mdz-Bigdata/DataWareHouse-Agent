"""门禁自身的可观测性：两类核心指标 + 四个闭环度量指标。

原文第六章「门禁运维：监控门禁本身」：

    | 指标                   | 说明                 | 阈值   | 级别    |
    | rejected_records_count | 被拒绝的记录数        | > 100  | WARNING |
    | quality_check_duration | 质量检查耗时（毫秒）  | > 1000 | WARNING |

    前者防「门禁误杀」——拒绝量突增往往意味着新规则写错了或者上游批量变更；
    后者防「门禁成瓶颈」——检查耗时超限会直接拖垮实时入湖延迟。

以及结语处的四个闭环度量指标：
**入湖拦截率、异常处理 SLA 达成率、修复重入湖成功率、同类异常复发率**，
全部沉淀至 dws / ads 层质量看板。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .isolation import IssueRecord, IssueStatus
from .severity import Disposition, IssueLevel, Severity
from .thresholds import (
    QUALITY_CHECK_DURATION_MS_THRESHOLD,
    REJECTED_RECORDS_COUNT_THRESHOLD,
)

__all__ = [
    "MetricAlert",
    "GateMetrics",
    "ClosedLoopMetrics",
    "METRIC_REJECTED_RECORDS_COUNT",
    "METRIC_QUALITY_CHECK_DURATION",
]

#: 原文第六章两类核心指标的指标名，逐字使用，不要改名——监控大屏按名取数
METRIC_REJECTED_RECORDS_COUNT = "rejected_records_count"
METRIC_QUALITY_CHECK_DURATION = "quality_check_duration"


@dataclass(frozen=True, slots=True)
class MetricAlert:
    """一条门禁自监控告警。级别固定 WARNING（原文两项指标都是 WARNING）。"""

    metric: str
    value: float
    threshold: float
    severity: Severity
    message: str
    observed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "message": self.message,
            "observed_at": self.observed_at.isoformat(sep=" ", timespec="seconds"),
        }


@dataclass(slots=True)
class GateMetrics:
    """门禁运行期计数器（线程安全）。

    一个实例对应一个统计窗口——实时作业按 checkpoint 窗口、批作业按一次跑批，
    窗口结束调 :meth:`evaluate` 拿告警、调 :meth:`snapshot` 落指标表。
    """

    window_started_at: datetime = field(default_factory=datetime.now)
    checked_records_count: int = 0
    accepted_records_count: int = 0
    flagged_records_count: int = 0
    rejected_records_count: int = 0
    shadow_hit_count: int = 0
    batch_check_count: int = 0
    total_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    last_duration_ms: float = 0.0
    #: 超过 quality_check_duration 阈值的检查次数
    slow_check_count: int = 0
    hits_by_rule: dict[str, int] = field(default_factory=dict)
    hits_by_level: dict[str, int] = field(default_factory=dict)
    rejected_by_table: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ---- 采集 ----

    def observe(
        self,
        *,
        disposition: Disposition,
        duration_ms: float,
        table: str,
        rule_ids: Sequence[str] = (),
        levels: Sequence[IssueLevel] = (),
        shadow_rule_ids: Sequence[str] = (),
    ) -> None:
        """登记一次门禁判定。门禁热路径上调用，必须保持 O(命中数)。"""
        with self._lock:
            self.checked_records_count += 1
            if disposition is Disposition.REJECT:
                self.rejected_records_count += 1
                self.rejected_by_table[table] = self.rejected_by_table.get(table, 0) + 1
            elif disposition is Disposition.ALLOW_WITH_FLAG:
                self.flagged_records_count += 1
            else:
                self.accepted_records_count += 1

            self.total_duration_ms += duration_ms
            self.last_duration_ms = duration_ms
            self.max_duration_ms = max(self.max_duration_ms, duration_ms)
            if duration_ms > QUALITY_CHECK_DURATION_MS_THRESHOLD:
                self.slow_check_count += 1

            for rid in rule_ids:
                self.hits_by_rule[rid] = self.hits_by_rule.get(rid, 0) + 1
            for lvl in levels:
                self.hits_by_level[lvl.value] = self.hits_by_level.get(lvl.value, 0) + 1
            self.shadow_hit_count += len(shadow_rule_ids)

    def observe_batch_check(self, *, duration_ms: float, rule_ids: Sequence[str] = ()) -> None:
        """登记一次批级检查（重复率、批次缺片等 BATCH 作用域规则）。"""
        with self._lock:
            self.batch_check_count += 1
            self.total_duration_ms += duration_ms
            self.max_duration_ms = max(self.max_duration_ms, duration_ms)
            for rid in rule_ids:
                self.hits_by_rule[rid] = self.hits_by_rule.get(rid, 0) + 1

    # ---- 派生 ----

    @property
    def avg_duration_ms(self) -> float:
        total = self.checked_records_count + self.batch_check_count
        return self.total_duration_ms / total if total else 0.0

    @property
    def reject_rate(self) -> float:
        """入湖拦截率（拒绝数 / 检查数）——四个闭环度量指标之一。"""
        return (
            self.rejected_records_count / self.checked_records_count
            if self.checked_records_count
            else 0.0
        )

    @property
    def flag_rate(self) -> float:
        """带标记放行率。"""
        return (
            self.flagged_records_count / self.checked_records_count
            if self.checked_records_count
            else 0.0
        )

    # ---- 告警 ----

    def evaluate(self, at: datetime | None = None) -> list[MetricAlert]:
        """按原文第六章两条阈值判定，返回需要发出的告警。

        判定用「严格大于」：原文写的是 > 100 和 > 1000，等于阈值不告警。
        """
        now = at or datetime.now()
        alerts: list[MetricAlert] = []
        if self.rejected_records_count > REJECTED_RECORDS_COUNT_THRESHOLD:
            alerts.append(
                MetricAlert(
                    metric=METRIC_REJECTED_RECORDS_COUNT,
                    value=float(self.rejected_records_count),
                    threshold=float(REJECTED_RECORDS_COUNT_THRESHOLD),
                    severity=Severity.WARNING,
                    message=(
                        f"被拒绝的记录数 {self.rejected_records_count} 超过阈值 "
                        f"{REJECTED_RECORDS_COUNT_THRESHOLD}（拒绝率 {self.reject_rate:.2%}）；"
                        f"排查方向：新规则写错 或 上游批量变更"
                    ),
                    observed_at=now,
                )
            )
        if self.max_duration_ms > QUALITY_CHECK_DURATION_MS_THRESHOLD:
            alerts.append(
                MetricAlert(
                    metric=METRIC_QUALITY_CHECK_DURATION,
                    value=float(self.max_duration_ms),
                    threshold=float(QUALITY_CHECK_DURATION_MS_THRESHOLD),
                    severity=Severity.WARNING,
                    message=(
                        f"质量检查耗时 {self.max_duration_ms:.1f}ms 超过阈值 "
                        f"{QUALITY_CHECK_DURATION_MS_THRESHOLD}ms（慢检查 {self.slow_check_count} 次，"
                        f"均值 {self.avg_duration_ms:.1f}ms）；门禁有成为瓶颈的风险，"
                        f"会直接拖垮实时入湖延迟"
                    ),
                    observed_at=now,
                )
            )
        return alerts

    # ---- 导出 ----

    def snapshot(self) -> dict[str, Any]:
        """指标快照，落 DWS/ADS 质量看板。"""
        return {
            "window_started_at": self.window_started_at.isoformat(sep=" ", timespec="seconds"),
            "checked_records_count": self.checked_records_count,
            "accepted_records_count": self.accepted_records_count,
            "flagged_records_count": self.flagged_records_count,
            METRIC_REJECTED_RECORDS_COUNT: self.rejected_records_count,
            METRIC_QUALITY_CHECK_DURATION: round(self.max_duration_ms, 3),
            "avg_quality_check_duration": round(self.avg_duration_ms, 3),
            "last_quality_check_duration": round(self.last_duration_ms, 3),
            "slow_check_count": self.slow_check_count,
            "shadow_hit_count": self.shadow_hit_count,
            "batch_check_count": self.batch_check_count,
            "reject_rate": round(self.reject_rate, 6),
            "flag_rate": round(self.flag_rate, 6),
            "hits_by_rule": dict(sorted(self.hits_by_rule.items(), key=lambda kv: -kv[1])),
            "hits_by_level": dict(sorted(self.hits_by_level.items())),
            "rejected_by_table": dict(
                sorted(self.rejected_by_table.items(), key=lambda kv: -kv[1])
            ),
        }

    def reset(self, at: datetime | None = None) -> None:
        """开启新的统计窗口。"""
        with self._lock:
            self.window_started_at = at or datetime.now()
            self.checked_records_count = 0
            self.accepted_records_count = 0
            self.flagged_records_count = 0
            self.rejected_records_count = 0
            self.shadow_hit_count = 0
            self.batch_check_count = 0
            self.total_duration_ms = 0.0
            self.max_duration_ms = 0.0
            self.last_duration_ms = 0.0
            self.slow_check_count = 0
            self.hits_by_rule.clear()
            self.hits_by_level.clear()
            self.rejected_by_table.clear()


@dataclass(frozen=True, slots=True)
class ClosedLoopMetrics:
    """四个闭环度量指标（原文结语）：

    入湖拦截率、异常处理 SLA 达成率、修复重入湖成功率、同类异常复发率。
    全部沉淀至 dws / ads 层质量看板，让「数据质量」本身成为可度量、可优化的对象。
    """

    intercept_rate: float
    sla_attainment_rate: float
    repair_reingest_success_rate: float
    recurrence_rate: float
    checked_records_count: int
    isolated_count: int
    closed_count: int
    reingested_count: int
    discarded_count: int

    @classmethod
    def compute(
        cls,
        issues: Sequence[IssueRecord],
        *,
        checked_records_count: int,
    ) -> ClosedLoopMetrics:
        """从隔离表记录 + 门禁检查总数算出四个指标。

        口径（⚠️ 原文只给了指标名没给公式，以下口径为本项目设计）：
          · 入湖拦截率        = 隔离记录数 / 门禁检查记录数
          · 异常处理 SLA 达成率 = 在 SLA 内闭环的记录数 / 已闭环且有 SLA 时限的记录数
          · 修复重入湖成功率   = 复验通过重入湖数 / （重入湖数 + 弃置归档数）
          · 同类异常复发率     = 同一 (表, 规则) 出现 ≥2 次的异常数 / 异常总数
        """
        isolated = len(issues)
        closed = [i for i in issues if i.issue_status.is_terminal]
        reingested = sum(1 for i in issues if i.issue_status is IssueStatus.REINGESTED)
        discarded = sum(1 for i in issues if i.issue_status is IssueStatus.DISCARDED)

        with_sla = [i for i in closed if i.sla_met is not None]
        sla_rate = (sum(1 for i in with_sla if i.sla_met) / len(with_sla)) if with_sla else 1.0

        repaired_total = reingested + discarded
        repair_rate = reingested / repaired_total if repaired_total else 0.0

        seen: dict[tuple[str, str], int] = {}
        for issue in issues:
            for rid in issue.rule_ids:
                key = (issue.source_table, rid)
                seen[key] = seen.get(key, 0) + 1
        recurring = sum(count for count in seen.values() if count >= 2)
        total_hits = sum(seen.values())
        recurrence = recurring / total_hits if total_hits else 0.0

        return cls(
            intercept_rate=(isolated / checked_records_count) if checked_records_count else 0.0,
            sla_attainment_rate=sla_rate,
            repair_reingest_success_rate=repair_rate,
            recurrence_rate=recurrence,
            checked_records_count=checked_records_count,
            isolated_count=isolated,
            closed_count=len(closed),
            reingested_count=reingested,
            discarded_count=discarded,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "入湖拦截率": round(self.intercept_rate, 6),
            "异常处理SLA达成率": round(self.sla_attainment_rate, 6),
            "修复重入湖成功率": round(self.repair_reingest_success_rate, 6),
            "同类异常复发率": round(self.recurrence_rate, 6),
            "checked_records_count": self.checked_records_count,
            "isolated_count": self.isolated_count,
            "closed_count": self.closed_count,
            "reingested_count": self.reingested_count,
            "discarded_count": self.discarded_count,
        }
