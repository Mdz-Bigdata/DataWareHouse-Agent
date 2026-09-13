"""五步异常闭环：拦截 → 隔离 → 告警 → 分流处置 → 复验重入湖。

原文第五章：

    ① 门禁拦截：阻断写入 ODS，携带原始报文 + 命中规则 ID
    ② 异常隔离：写入隔离表 ods_quality_issue，原始数据不丢失、可重放
    ③ 分级告警：P0 电话 + 钉钉 · P1 钉钉 + 工单 · P2 日报 · P3 周报，
       通知数据 owner + 平台值班
    ④ 分流处置：A 自动修复（重传 / 幂等重放 / 断点续传）、B 人工修复（源端补数，
       工单跟踪）、C 弃置归档（无法修复，标记原因后归档保留审计）
    ⑤ 复验重入湖：重新执行全部门禁规则，通过则写入 ODS 并回填处理状态；
       不通过退回隔离，超 3 轮升级 P0

第 ①②步在 :mod:`.gate`（检查器热路径），本模块负责 ③④⑤ 并把五步串成一个可调用的闭环。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .alerting import AlertRouter
from .gate import BatchResult, GateDecision, QualityGate
from .isolation import IssueRecord, IssueStatus, IssueStore
from .metrics import ClosedLoopMetrics
from .rules import RepairAction
from .severity import Channel, IssueLevel
from .thresholds import MAX_RECHECK_ROUNDS

__all__ = [
    "RepairOutcome",
    "RecheckResult",
    "RepairStrategy",
    "ReplayRepair",
    "ClosedLoop",
]

_log = logging.getLogger("adas.quality.closed_loop")


class OdsSink(Protocol):
    """复验通过后写 ODS 的出口。"""

    def __call__(self, table: str, record: Mapping[str, Any]) -> None:  # pragma: no cover
        ...


class RepairStrategy(Protocol):
    """自动修复策略（原文 ④-A：重传 / 幂等重放 / 断点续传）。

    返回修复后的记录；返回 None 表示这条修不了，应转人工或弃置。
    """

    def repair(self, issue: IssueRecord) -> Mapping[str, Any] | None:  # pragma: no cover
        ...


class ReplayRepair:
    """默认自动修复策略：幂等重放。

    直接把隔离表里的原始报文原样重放一次——对「瞬时抖动、上游已自行修正、
    依赖服务当时不可用」这三类异常有效，且因为 artifact_id 由内容哈希派生，
    重放天然幂等（见共享契约 ids 模块规则一）。

    ⚠️ 原文未明确，本项目设计：原文的「重传 / 断点续传」需要对接对象存储与车端
    回传服务，属于通道侧能力，这里以策略接口留出扩展点，不做假实现。
    """

    def repair(self, issue: IssueRecord) -> Mapping[str, Any] | None:
        try:
            return issue.replay_payload()
        except ValueError as exc:
            _log.info("隔离记录 %s 不可重放: %s", issue.issue_id, exc)
            return None


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """一次分流处置的结果。"""

    issue: IssueRecord
    action: RepairAction
    repaired: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue.issue_id,
            "action": self.action.value,
            "action_cn": self.action.name_cn,
            "repaired": self.repaired,
            "status": self.issue.issue_status.value,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class RecheckResult:
    """一次复验重入湖的结果。"""

    issue: IssueRecord
    passed: bool
    decision: GateDecision | None
    escalated: bool
    round_no: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue.issue_id,
            "passed": self.passed,
            "round_no": self.round_no,
            "escalated": self.escalated,
            "status": self.issue.issue_status.value,
            "issue_level": self.issue.issue_level.value,
            "hits": [h.rule_id for h in (self.decision.hits if self.decision else ())],
        }


class ClosedLoop:
    """五步异常闭环的编排器。

    :param gate: 门禁（①② 步）
    :param router: 分级告警路由（③ 步），缺省新建一个打日志的
    :param ods_sink: 复验通过后写 ODS 的出口（⑤ 步）；不给则只回填状态不落库
    :param auto_repair: 自动修复策略（④-A），缺省幂等重放
    :param clock: 取时间的钩子，便于测试

    典型用法::

        loop = ClosedLoop(QualityGate(...), ods_sink=my_sink)
        result = loop.ingest("ods_data_file_meta", records, channel=Channel.OSS_FILE)
        for issue in result.issues:                 # ④ 分流处置
            loop.dispatch(issue)
        loop.run_recheck_queue()                    # ⑤ 复验重入湖
    """

    def __init__(
        self,
        gate: QualityGate,
        *,
        router: AlertRouter | None = None,
        ods_sink: OdsSink | Callable[[str, Mapping[str, Any]], None] | None = None,
        auto_repair: RepairStrategy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gate = gate
        self.router = router if router is not None else AlertRouter()
        self.ods_sink = ods_sink
        self.auto_repair = auto_repair if auto_repair is not None else ReplayRepair()
        self.clock = clock or gate.clock

    @property
    def store(self) -> IssueStore:
        return self.gate.store

    # ------------------------------------------------------------------ ①②③
    def ingest(
        self,
        table: str,
        records: Sequence[Mapping[str, Any]],
        *,
        channel: Channel = Channel.COMMON,
        batch_stats: Mapping[str, Any] | None = None,
        alert: bool = True,
    ) -> BatchResult:
        """跑完一批数据的 ①拦截 → ②隔离 → ③告警。

        通过的记录（含带标记放行）由调用方从 :meth:`BatchResult.accepted_rows` 取走写 ODS；
        被拒的已在隔离表里等待 ④ 分流处置。

        ③ 覆盖三类命中，一类都不能漏（各自的去向见对应私有方法）：

        · 被拒绝的 → 已落隔离表，按等级即时触达或入汇总队列，并回写 ALERTED 状态
        · 带标记放行的 → 不进隔离表，但要进 P2 日报 / P3 周报，否则原文的 SLA 没有对象
        · 批级命中的 → 不拦截数据，同样进汇总队列（重复率 > 5%、批次缺片）
        """
        result = self.gate.check_batch(table, records, channel=channel, batch_stats=batch_stats)
        if not alert:
            return result
        now = self.clock()
        if result.issues:
            # 同等级合并成一条发出，避免同批次刷屏；随后逐条把 ③ 落到隔离记录上
            self.router.notify_many(result.issues)
            for issue in result.issues:
                self._note_alerted(issue, now)
        self._digest_flagged(result, records, now)
        self._digest_batch_hits(result, table, channel, now)
        return result

    def _note_alerted(self, issue: IssueRecord, now: datetime) -> None:
        """把 ③ 分级告警这一步写进隔离记录的状态与轨迹。

        过去这件事只有单条补发的 :meth:`alert` 会做，批量入湖这条主路径发完告警
        就散了：隔离记录一直停在 ISOLATED，事后审计看不出「到底通知过没有」。
        """
        policy = issue.issue_level.policy
        immediate = issue.issue_level in (IssueLevel.P0, IssueLevel.P1)
        issue.issue_status = IssueStatus.ALERTED
        issue.note(
            f"③ 分级告警：{issue.issue_level.value} "
            f"{'/'.join(policy.notify_channels)}，{policy.response_requirement_cn}"
            + ("" if immediate else "（已入汇总队列，待日报 / 周报发出）"),
            now,
        )
        self.store.upsert(issue)

    def _digest_flagged(
        self, result: BatchResult, records: Sequence[Mapping[str, Any]], now: datetime
    ) -> None:
        """带标记放行的命中也要触达，否则 P2 的 SLA 根本无从达成。

        原文第五章 SLA 表把「时间同步超限、定位跳变、关联缺失、状态流转异常」这批
        WARNING 明确列在 P2「日报汇总，3 个工作日内闭环」——只往记录上写一个
        ``_quality_flag`` 而不进汇总，日报永远是空的，「3 个工作日内闭环」也就没有对象。

        这些记录已经进了 ODS（原文：带标记放行不阻塞主链路），因此这里构造的是
        **临时**隔离记录，只用来喂告警汇总队列，不写隔离表——隔离表是「被拦下的数据」
        的地方，掺进放行数据会把入湖拦截率的口径搞脏。
        """
        owner_of = self.gate.owner_resolver
        for decision, record in zip(result.decisions, records, strict=True):
            if not decision.flagged:
                continue
            if decision.issue is not None:
                # 门禁开了 isolate_warnings：这条已经随 result.issues 走过 ③，别发两遍
                continue
            issue = IssueRecord.from_hits(
                table=decision.table,
                channel=decision.channel,
                record=record,
                hits=list(decision.hits),
                detected_at=decision.decided_at,
                record_key=decision.record_key,
                source_system=self.gate.source_system,
                owner=owner_of(decision.table) if owner_of else "",
                on_duty=self.gate.on_duty,
            )
            issue.note(
                f"③ 分级告警：带标记放行（{decision.quality_flag}），数据已入湖，"
                f"{issue.issue_level.policy.response_requirement_cn}",
                now,
            )
            self.router.notify(issue)

    def _digest_batch_hits(
        self, result: BatchResult, table: str, channel: Channel, now: datetime
    ) -> None:
        """批级命中（重复率 > 5%、批次缺片）走告警路由，而不是只落一行日志。

        原文 4.2 对重复率写的是「自动去重 + **超限告警**」，第五章把「重复率波动、
        批次轻微缺片」列在 P3「周报汇总」。判据是批统计量而不是某一条记录，
        所以同样不进隔离表，只构造临时记录喂汇总队列——报文就是这批的统计量本身。
        """
        for hit in result.batch_hits:
            if hit.shadow:
                continue
            _log.warning(
                "批级规则命中 [%s] %s: %s（%s）", hit.rule_id, hit.message, hit.detail, table
            )
            issue = IssueRecord.from_hits(
                table=table,
                channel=channel,
                record=dict(result.batch_stats),
                hits=[hit],
                detected_at=now,
                record_key=f"batch:{table}:{now.isoformat(sep=' ', timespec='seconds')}",
                source_system=self.gate.source_system,
                on_duty=self.gate.on_duty,
            )
            #: 批统计量不是一条可重放的业务记录——复验重入湖对它没有意义
            issue.replayable = False
            issue.note(f"③ 分级告警：批级命中，不拦截数据（{hit.detail}）", now)
            self.router.notify(issue)

    def alert(self, issue: IssueRecord) -> Any:
        """③ 分级告警：单条补发（门禁热路径未配 notifier 时用）。

        状态与轨迹的回写走 :meth:`_note_alerted`，与批量入湖那条路径共用同一段实现——
        两处各写一遍迟早写出两种说法。
        """
        alert = self.router.notify(issue)
        self._note_alerted(issue, self.clock())
        return alert

    def mark_responded(self, issue: IssueRecord, at: datetime | None = None) -> IssueRecord:
        """登记「已响应」，用于异常处理 SLA 达成率。"""
        now = at or self.clock()
        issue.responded_at = now
        due = issue.response_due_at
        on_time = due is None or now <= due
        issue.note(
            f"响应登记：{'按时' if on_time else '超时'}"
            + (f"（截止 {due.isoformat(sep=' ', timespec='seconds')}）" if due else ""),
            now,
        )
        self.store.upsert(issue)
        return issue

    # ------------------------------------------------------------------ ④
    def dispatch(
        self,
        issue: IssueRecord,
        action: RepairAction | None = None,
        *,
        repaired_record: Mapping[str, Any] | None = None,
        reason: str = "",
    ) -> RepairOutcome:
        """④ 分流处置：A 自动修复 / B 人工修复 / C 弃置归档。

        :param action: 不给则用命中规则声明的 ``repair_action``（声明式路由）
        :param repaired_record: 人工修复回来的记录（B 分支）
        :param reason: 弃置原因（C 分支必填，原文要求「标记原因后归档保留审计」）
        """
        now = self.clock()
        chosen = action or issue.repair_action or RepairAction.MANUAL_REPAIR
        issue.repair_action = chosen
        issue.issue_status = IssueStatus.DISPATCHED
        issue.note(f"④ 分流处置：{chosen.name_cn}", now)

        handlers: dict[RepairAction, Callable[[], RepairOutcome]] = {
            RepairAction.AUTO_REPAIR: lambda: self._auto_repair(issue, now),
            RepairAction.MANUAL_REPAIR: lambda: self._manual_repair(issue, repaired_record, now),
            RepairAction.DISCARD_ARCHIVE: lambda: self._discard(issue, reason, now),
        }
        outcome = handlers[chosen]()
        self.store.upsert(issue)
        return outcome

    def _auto_repair(self, issue: IssueRecord, now: datetime) -> RepairOutcome:
        """A 自动修复：重传 / 幂等重放 / 断点续传。"""
        repaired = self.auto_repair.repair(issue)
        if repaired is None:
            issue.issue_status = IssueStatus.DISPATCHED
            # 真的把处置分支改成 B，否则下一次 dispatch(issue)（不显式传 action）
            # 会再按声明的 A 跑一遍——重放失败是确定性的，重试多少次都还是失败
            issue.repair_action = RepairAction.MANUAL_REPAIR
            issue.note("④-A 自动修复失败，转 B 人工修复", now)
            return RepairOutcome(issue, RepairAction.AUTO_REPAIR, False, "自动修复失败，需转人工")
        issue.raw_payload = _dump_payload(repaired)
        issue.issue_status = IssueStatus.REPAIRED
        issue.note("④-A 自动修复完成（幂等重放），进入复验队列", now)
        return RepairOutcome(issue, RepairAction.AUTO_REPAIR, True, "自动修复完成")

    def _manual_repair(
        self, issue: IssueRecord, repaired_record: Mapping[str, Any] | None, now: datetime
    ) -> RepairOutcome:
        """B 人工修复：源端补数，工单跟踪。"""
        if repaired_record is None:
            issue.note("④-B 人工修复：已建工单，等待源端补数", now)
            return RepairOutcome(issue, RepairAction.MANUAL_REPAIR, False, "等待源端补数")
        issue.raw_payload = _dump_payload(repaired_record)
        issue.issue_status = IssueStatus.REPAIRED
        issue.note("④-B 人工修复完成（源端补数已回填），进入复验队列", now)
        return RepairOutcome(issue, RepairAction.MANUAL_REPAIR, True, "人工修复完成")

    def _discard(self, issue: IssueRecord, reason: str, now: datetime) -> RepairOutcome:
        """C 弃置归档：无法修复，标记原因后归档保留审计。"""
        if not reason:
            reason = "无法修复（未填写具体原因）"
        issue.discard_reason = reason
        issue.issue_status = IssueStatus.DISCARDED
        issue.resolved_at = now
        issue.sla_met = _sla_met(issue, now)
        issue.note(f"④-C 弃置归档：{reason}（原始报文保留，供审计）", now)
        return RepairOutcome(issue, RepairAction.DISCARD_ARCHIVE, False, f"弃置归档：{reason}")

    # ------------------------------------------------------------------ ⑤
    def recheck(self, issue: IssueRecord) -> RecheckResult:
        """⑤ 复验重入湖：重新执行**全部**门禁规则。

        通过 → 写入 ODS 并回填处理状态（REINGESTED）；
        不通过 → 退回隔离（ISOLATED，recheck_count += 1），
        超 3 轮（thresholds.MAX_RECHECK_ROUNDS）升级 P0。
        """
        now = self.clock()
        issue.issue_status = IssueStatus.RECHECKING
        round_no = issue.recheck_count + 1

        try:
            payload = issue.replay_payload()
        except ValueError as exc:
            issue.issue_status = IssueStatus.ISOLATED
            issue.recheck_count = round_no
            issue.note(f"⑤ 复验失败：报文不可重放（{exc}）", now)
            escalated = self._escalate_if_needed(issue, now)
            self.store.upsert(issue)
            return RecheckResult(issue, False, None, escalated, round_no)

        channel = issue.source_channel
        # isolate=False：复验走同一套规则，但不再刷新的隔离记录
        decision = self.gate.check(issue.source_table, payload, channel=channel, isolate=False)
        if decision.accepted:
            if self.ods_sink is not None:
                self.ods_sink(issue.source_table, decision.apply_flag(payload))
            issue.issue_status = IssueStatus.REINGESTED
            issue.recheck_count = round_no
            issue.reingested_at = now
            issue.resolved_at = now
            issue.sla_met = _sla_met(issue, now)
            flag = decision.quality_flag or "（无质量标记）"
            issue.note(
                f"⑤ 复验通过（第 {round_no} 轮），已重入湖 {issue.source_table}，标记 {flag}", now
            )
            self.store.upsert(issue)
            return RecheckResult(issue, True, decision, False, round_no)

        issue.issue_status = IssueStatus.ISOLATED
        issue.recheck_count = round_no
        issue.note(
            f"⑤ 复验不通过（第 {round_no} 轮），退回隔离：命中 "
            f"{', '.join(h.rule_id for h in decision.hits)}",
            now,
        )
        escalated = self._escalate_if_needed(issue, now)
        self.store.upsert(issue)
        return RecheckResult(issue, False, decision, escalated, round_no)

    def _escalate_if_needed(self, issue: IssueRecord, now: datetime) -> bool:
        """超 3 轮升级 P0（原文第五章第 ⑤ 步）。

        严格大于 3 轮才升级：第 1/2/3 轮复验失败仍按原等级处理，
        第 4 轮起判定为「修不好」，升 P0 并即时告警。
        """
        if issue.recheck_count <= MAX_RECHECK_ROUNDS or issue.escalated:
            return False
        previous = issue.issue_level
        issue.issue_level = IssueLevel.P0
        issue.escalated = True
        policy = IssueLevel.P0.policy
        issue.response_due_at = policy.response_due_at(now)
        issue.closure_due_at = policy.closure_due_at(now)
        moved = (
            "保持 P0 并再次触达" if previous is IssueLevel.P0 else f"由 {previous.value} 升级为 P0"
        )
        issue.note(
            f"⑤ 复验已超 {MAX_RECHECK_ROUNDS} 轮（当前第 {issue.recheck_count} 轮），{moved}",
            now,
        )
        self.router.notify(issue, escalated_from=previous)
        return True

    def run_recheck_queue(self, limit: int | None = None) -> list[RecheckResult]:
        """把隔离表里「已修复待复验」的记录批量跑一遍复验。"""
        pending = self.store.pending_recheck()
        if limit is not None:
            pending = pending[:limit]
        return [self.recheck(issue) for issue in pending]

    # ------------------------------------------------------------------ 度量
    def metrics(self) -> ClosedLoopMetrics:
        """四个闭环度量指标（入湖拦截率 / SLA 达成率 / 重入湖成功率 / 复发率）。"""
        return ClosedLoopMetrics.compute(
            list(self.store.iter_all()),
            checked_records_count=self.gate.metrics.checked_records_count,
        )

    def daily_report(self, at: datetime | None = None) -> dict[str, Any]:
        """日报：P2 汇总 + 门禁自监控 + 闭环度量。"""
        now = at or self.clock()
        alert = self.router.flush_daily(now)
        return {
            "generated_at": now.isoformat(sep=" ", timespec="seconds"),
            "gate_metrics": self.gate.metrics.snapshot(),
            "gate_metric_alerts": [a.to_dict() for a in self.gate.metrics.evaluate(now)],
            "closed_loop_metrics": self.metrics().to_dict(),
            "p2_digest": alert.render() if alert else "",
            "pending": self.router.pending_digest_counts(),
        }

    def weekly_report(self, at: datetime | None = None) -> dict[str, Any]:
        """周报：P3 汇总（含连续两周超标升级 P2）+ 规则运行统计。"""
        now = at or self.clock()
        alert = self.router.flush_weekly(now)
        return {
            "generated_at": now.isoformat(sep=" ", timespec="seconds"),
            "p3_digest": alert.render() if alert else "",
            "rule_stats": self.gate.center.stats_snapshot(),
            "closed_loop_metrics": self.metrics().to_dict(),
        }


def _dump_payload(record: Mapping[str, Any]) -> str:
    import json

    return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)


def _sla_met(issue: IssueRecord, closed_at: datetime) -> bool:
    """闭环是否达成 SLA。

    优先看闭环时限（P1 当日修复 / P2 3 个工作日），没有闭环时限的（P0/P3）
    退化为看响应时限；两者都没有则视为达成。
    """
    due = issue.closure_due_at or issue.response_due_at
    if due is None:
        return True
    return closed_at <= due
