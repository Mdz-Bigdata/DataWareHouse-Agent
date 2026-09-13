"""门禁执行器：检查器三分支 + 第 ① 步拦截 + 第 ② 步隔离（recordRejectedData）。

原文第三章：

    检查器实现非常克制——遍历该表注册的规则，按严重程度决定三种出口
    ERROR → REJECT  |  WARNING → ALLOW_WITH_FLAG  |  通过 → ACCEPTED

    ⚠️ 注意 recordRejectedData 这一步：被拒绝的数据连同命中规则一起落表，
      而不是打日志了事。原始数据不丢失是整套门禁可重放、可审计的根基。

本模块只做「拦」与「落隔离表」两件事；告警、分流处置、复验重入湖在
:mod:`.closed_loop`。这样切分是为了让门禁热路径足够薄——原文第六章
quality_check_duration > 1000ms 就要告警，检查器里不能挂重活。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .isolation import InMemoryIssueStore, IssueRecord, IssueStore
from .metrics import GateMetrics
from .rules import CheckContext, DuplicateTracker, RuleHit, RuleScope
from .ruleset import RuleCenter
from .severity import (
    DISPOSITION_BY_SEVERITY,
    QUALITY_FLAG_FIELD,
    Channel,
    Disposition,
    IssueLevel,
    Severity,
)

__all__ = [
    "GateDecision",
    "BatchResult",
    "QualityGate",
    "format_quality_flag",
]


class _Notifier(Protocol):
    """告警器的结构化协议（避免 gate ↔ alerting 硬耦合）。"""

    def notify(self, issue: IssueRecord) -> Any:  # pragma: no cover - 协议声明
        ...


def format_quality_flag(hits: Sequence[RuleHit]) -> str:
    """生成 ``_quality_flag`` 的取值。

    原文第二章只说「带标记放行的数据写入 _quality_flag，供下游按质量筛选」，
    没规定格式。⚠️ 原文未明确，本项目设计：用
    ``{等级}/{维度}:{规则ID}`` 分号拼接，既能被 LIKE 过滤，也能被 split 解析。
    例：``P2/accuracy:QG-OSS-004-time-sync-collect-vehicle;P3/uniqueness:QG-KFK-005-event-id-dedup``
    """
    parts = [f"{h.issue_level.value}/{h.dimension.key}:{h.rule_id}" for h in hits]
    return ";".join(sorted(parts))


@dataclass(frozen=True, slots=True)
class GateDecision:
    """一条记录的门禁判定结果。"""

    table: str
    channel: Channel
    record_key: str
    disposition: Disposition
    hits: tuple[RuleHit, ...]
    shadow_hits: tuple[RuleHit, ...]
    quality_flag: str
    duration_ms: float
    checked_rule_count: int
    issue: IssueRecord | None = None
    decided_at: datetime = field(default_factory=datetime.now)

    @property
    def rejected(self) -> bool:
        return self.disposition is Disposition.REJECT

    @property
    def accepted(self) -> bool:
        """通过门禁（含带标记放行）——可以写 ODS。"""
        return self.disposition is not Disposition.REJECT

    @property
    def flagged(self) -> bool:
        return self.disposition is Disposition.ALLOW_WITH_FLAG

    @property
    def worst_level(self) -> IssueLevel | None:
        if not self.hits:
            return None
        return min((h.issue_level for h in self.hits), key=lambda lvl: lvl.value)

    def apply_flag(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """返回带 ``_quality_flag`` 的记录副本，供下游按质量筛选。"""
        out = dict(record)
        out[QUALITY_FLAG_FIELD] = self.quality_flag
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "channel": self.channel.value,
            "record_key": self.record_key,
            "disposition": self.disposition.value,
            "quality_flag": self.quality_flag,
            "duration_ms": round(self.duration_ms, 3),
            "checked_rule_count": self.checked_rule_count,
            "hits": [h.to_dict() for h in self.hits],
            "shadow_hits": [h.to_dict() for h in self.shadow_hits],
            "issue_id": self.issue.issue_id if self.issue else None,
            "decided_at": self.decided_at.isoformat(sep=" ", timespec="milliseconds"),
        }


@dataclass(frozen=True, slots=True)
class BatchResult:
    """一批记录的门禁结果。"""

    table: str
    channel: Channel
    decisions: tuple[GateDecision, ...]
    batch_hits: tuple[RuleHit, ...]
    batch_stats: Mapping[str, Any]

    @property
    def accepted(self) -> tuple[GateDecision, ...]:
        return tuple(d for d in self.decisions if d.accepted)

    @property
    def rejected(self) -> tuple[GateDecision, ...]:
        return tuple(d for d in self.decisions if d.rejected)

    @property
    def flagged(self) -> tuple[GateDecision, ...]:
        return tuple(d for d in self.decisions if d.flagged)

    @property
    def issues(self) -> tuple[IssueRecord, ...]:
        return tuple(d.issue for d in self.decisions if d.issue is not None)

    def accepted_rows(self, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """把通过门禁的记录按顺序取出来并打好 ``_quality_flag``，可直接写 ODS。"""
        out: list[dict[str, Any]] = []
        for decision, record in zip(self.decisions, records, strict=True):
            if decision.accepted:
                out.append(decision.apply_flag(record))
        return out


class QualityGate:
    """入湖质量门禁。

    :param center: 规则中心，缺省装载内置规则集（:mod:`.builtin`）
    :param store: 隔离表存储，缺省进程内实现
    :param notifier: 可选告警器（结构化协议，见 :mod:`.alerting`）
    :param metrics: 指标收集器，缺省新建
    :param source_system: ODS 层 ``_source_system`` 的取值，随隔离记录落表
    :param owner_resolver: 表名 → 数据 owner，用于分级告警的通知对象
    :param context_factory: 生成 :class:`~.rules.CheckContext` 的钩子，
        用来注入跨源关联查询、文件探针、自定义谓词等外部能力

    典型用法::

        gate = QualityGate(source_system="kafka:vehicle.trigger.event")
        decision = gate.check("ods_vehicle_trigger_event", record, channel=Channel.KAFKA)
        if decision.accepted:
            sink.write(decision.apply_flag(record))
    """

    def __init__(
        self,
        center: RuleCenter | None = None,
        *,
        store: IssueStore | None = None,
        notifier: _Notifier | None = None,
        metrics: GateMetrics | None = None,
        source_system: str = "",
        on_duty: str = "",
        owner_resolver: Callable[[str], str] | None = None,
        context_factory: Callable[[str, Channel], CheckContext] | None = None,
        clock: Callable[[], datetime] = datetime.now,
        isolate_warnings: bool = False,
    ) -> None:
        if center is None:
            from .builtin import default_rule_center

            center = default_rule_center()
        self.center = center
        self.store = store if store is not None else InMemoryIssueStore()
        self.notifier = notifier
        self.metrics = metrics if metrics is not None else GateMetrics()
        self.source_system = source_system
        self.on_duty = on_duty
        self.owner_resolver = owner_resolver
        self.context_factory = context_factory
        self.clock = clock
        #: ⚠️ 原文未明确，本项目设计：原文的五步闭环只针对「被拒绝」的数据。
        #: 打开这个开关会把 WARNING 命中也写隔离表（只做留痕，不阻断入湖）。
        self.isolate_warnings = isolate_warnings
        self._duplicates = DuplicateTracker()

    # ---- 上下文 ----

    def make_context(self, table: str, channel: Channel) -> CheckContext:
        """构造检查上下文。外部能力通过 context_factory 注入。"""
        if self.context_factory is not None:
            ctx = self.context_factory(table, channel)
            ctx.table = table
            ctx.channel = channel
        else:
            ctx = CheckContext(table=table, channel=channel)
        ctx.now = self.clock()
        ctx.duplicates = self._duplicates
        return ctx

    @staticmethod
    def record_key_of(record: Mapping[str, Any]) -> str:
        """取记录键：优先业务主键，兜底用报文哈希。

        这个键有两个用途：灰度抽样的稳定性，以及隔离记录的幂等 ID。
        """
        for candidate in ("data_id", "event_id", "artifact_id", "file_id", "id"):
            val = record.get(candidate)
            if val not in (None, ""):
                return str(val)
        from ..ids import content_hash

        return content_hash(
            json.dumps(record, ensure_ascii=False, sort_keys=True, default=str), length=16
        )

    # ---- 单条检查（热路径）----

    def check(
        self,
        table: str,
        record: Mapping[str, Any],
        *,
        channel: Channel = Channel.COMMON,
        context: CheckContext | None = None,
        isolate: bool = True,
    ) -> GateDecision:
        """对一条记录执行门禁。

        :param isolate: 命中拒绝规则时是否写隔离表。仅在「复验重入湖」时置 False——
            复验走的是同一套规则，但不该再刷一条新的隔离记录。
        """
        started = time.perf_counter()
        ctx = context or self.make_context(table, channel)
        ctx.record = record
        key = self.record_key_of(record)

        hits: list[RuleHit] = []
        shadows: list[RuleHit] = []
        rules = self.center.rules_for(table, channel, RuleScope.RECORD)
        for rule in rules:
            run, enforce = self.center.should_enforce(rule, table, key)
            if not run:
                continue
            self.center.note_evaluated(rule.rule_id)
            hit = rule.evaluate(record, ctx)
            if hit is None:
                continue
            if enforce:
                hits.append(hit)
                self.center.note_hit(rule.rule_id)
            else:
                shadows.append(hit.as_shadow())
                self.center.note_hit(rule.rule_id, shadow=True)

        disposition = self._decide(hits)
        duration_ms = (time.perf_counter() - started) * 1000.0

        issue: IssueRecord | None = None
        should_isolate = isolate and (
            disposition is Disposition.REJECT or (self.isolate_warnings and hits)
        )
        if should_isolate:
            issue = self._record_rejected_data(table, channel, record, hits, key, ctx.now)

        decision = GateDecision(
            table=table,
            channel=channel,
            record_key=key,
            disposition=disposition,
            hits=tuple(hits),
            shadow_hits=tuple(shadows),
            quality_flag=format_quality_flag(hits)
            if disposition is Disposition.ALLOW_WITH_FLAG
            else "",
            duration_ms=duration_ms,
            checked_rule_count=len(rules),
            issue=issue,
            decided_at=ctx.now,
        )
        self.metrics.observe(
            disposition=disposition,
            duration_ms=duration_ms,
            table=table,
            rule_ids=[h.rule_id for h in hits],
            levels=[h.issue_level for h in hits],
            shadow_rule_ids=[h.rule_id for h in shadows],
        )
        return decision

    @staticmethod
    def _decide(hits: Sequence[RuleHit]) -> Disposition:
        """严重程度决定处置——门禁里唯一一处做处置判定的地方。"""
        if not hits:
            return Disposition.ACCEPTED
        if any(h.severity is Severity.ERROR for h in hits):
            return DISPOSITION_BY_SEVERITY[Severity.ERROR]
        return DISPOSITION_BY_SEVERITY[Severity.WARNING]

    def _record_rejected_data(
        self,
        table: str,
        channel: Channel,
        record: Mapping[str, Any],
        hits: Sequence[RuleHit],
        key: str,
        at: datetime,
    ) -> IssueRecord:
        """原文第三章的 recordRejectedData：被拒绝的数据连同命中规则一起落表。

        写隔离表失败时不会吞异常，而是把失败写进记录轨迹后抛出——
        「原始数据不丢失」是这套门禁的根基，静默失败等于门禁失效。
        """
        owner = self.owner_resolver(table) if self.owner_resolver else ""
        issue = IssueRecord.from_hits(
            table=table,
            channel=channel,
            record=record,
            hits=list(hits),
            detected_at=at,
            record_key=key,
            source_system=self.source_system,
            owner=owner,
            on_duty=self.on_duty,
        )
        issue.note(f"① 门禁拦截：命中 {', '.join(issue.rule_ids)}，阻断写入 ODS", at)
        issue.note("② 异常隔离：原始报文已随记录保存，可重放", at)
        self.store.upsert(issue)
        if self.notifier is not None:
            try:
                self.notifier.notify(issue)
            except Exception:  # noqa: BLE001 - 告警失败不影响拦截与隔离
                issue.note("③ 分级告警：发送失败，已降级为仅隔离")
        return issue

    # ---- 批量检查 ----

    def check_batch(
        self,
        table: str,
        records: Sequence[Mapping[str, Any]],
        *,
        channel: Channel = Channel.COMMON,
        batch_stats: Mapping[str, Any] | None = None,
    ) -> BatchResult:
        """对一批记录执行门禁，并在批末执行 BATCH 作用域规则。

        BATCH 规则（重复率 > 5%、批次缺片等）看的是批统计量：调用方给的
        ``batch_stats`` 会与门禁自算的去重统计合并，调用方的值优先。
        """
        ctx = self.make_context(table, channel)
        decisions = [self.check(table, r, channel=channel, context=ctx) for r in records]

        stats: dict[str, Any] = dict(self._duplicates.stats(table))
        stats.update(
            {
                "checked_records_count": len(records),
                "rejected_records_count": sum(1 for d in decisions if d.rejected),
                "flagged_records_count": sum(1 for d in decisions if d.flagged),
                "reject_rate": (
                    sum(1 for d in decisions if d.rejected) / len(records) if records else 0.0
                ),
            }
        )
        if batch_stats:
            stats.update(batch_stats)

        batch_hits = self.check_batch_stats(table, stats, channel=channel, context=ctx)
        return BatchResult(
            table=table,
            channel=channel,
            decisions=tuple(decisions),
            batch_hits=tuple(batch_hits),
            batch_stats=stats,
        )

    def check_batch_stats(
        self,
        table: str,
        batch_stats: Mapping[str, Any],
        *,
        channel: Channel = Channel.COMMON,
        context: CheckContext | None = None,
    ) -> list[RuleHit]:
        """只跑 BATCH 作用域规则（重复率监控、批次到达完整性）。

        批级命中不拦截单条记录——原文把这类问题定为「自动去重 + 超限告警」
        与「监控告警（不拦截）」，所以这里只产出命中，由调用方决定告警。
        """
        started = time.perf_counter()
        ctx = context or self.make_context(table, channel)
        ctx.batch_stats = batch_stats
        hits: list[RuleHit] = []
        rules = self.center.rules_for(table, channel, RuleScope.BATCH)
        for rule in rules:
            run, enforce = self.center.should_enforce(rule, table, f"batch:{table}")
            if not run:
                continue
            self.center.note_evaluated(rule.rule_id)
            hit = rule.evaluate({}, ctx)
            if hit is None:
                continue
            self.center.note_hit(rule.rule_id, shadow=not enforce)
            hits.append(hit if enforce else hit.as_shadow())
        self.metrics.observe_batch_check(
            duration_ms=(time.perf_counter() - started) * 1000.0,
            rule_ids=[h.rule_id for h in hits if not h.shadow],
        )
        return hits

    # ---- 运维 ----

    def metric_alerts(self) -> list[Any]:
        """门禁自监控告警（rejected_records_count / quality_check_duration）。"""
        return self.metrics.evaluate(self.clock())

    def reset_window(self) -> None:
        """开启新的统计窗口，同时清空去重台账。"""
        self.metrics.reset(self.clock())
        self._duplicates.reset()

    def describe_rules(self, table: str | None = None) -> list[dict[str, object]]:
        """当前生效的规则清单，用于数据管理平台展示。"""
        rows = self.center.describe()
        if table is None:
            return rows
        return [r for r in rows if r["table"] in (table, "*")]


def iter_accepted(
    gate: QualityGate,
    table: str,
    records: Iterable[Mapping[str, Any]],
    *,
    channel: Channel = Channel.COMMON,
) -> Iterable[dict[str, Any]]:
    """流式门禁：逐条判定，只把通过的记录（已打 _quality_flag）交给下游。

    适合 Flink/PyFlink 的 map 算子里逐条调用——被拒绝的记录已进隔离表，
    这里直接跳过，主链路不被阻塞。
    """
    for record in records:
        decision = gate.check(table, record, channel=channel)
        if decision.accepted:
            yield decision.apply_flag(record)
