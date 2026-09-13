"""日级调度闭环：扫描决策 → 演练审计 → 执行 → 回写 → 告警。

来源：a14.md 第五章「执行机制：日级调度闭环 + 四道安全闸」。

五步的执行者原文分得很细（StarRocks 定时任务 / 治理演练服务 / 存储执行服务 /
告警服务），本模块把五步编排在一起，每一步都可以单独调用与单测：

    run = GovernanceRun(repo, executor=...)
    plan   = run.step1_scan(...)         # ① 扫描决策（StarRocks 定时任务 T+1）
    review = run.step2_rehearse(plan)    # ② 演练审计（大批量需人工确认）
    result = run.step3_execute(review)   # ③ 执行（限速、断点续做）
    run.step4_writeback(result, ...)     # ④ 回写（状态表 + 成本日表）
    alerts = run.step5_alert(result, ...)# ⑤ 告警

``run_daily()`` 把五步串成一次完整调度。默认 ``dry_run=True``——
敢对生产数据动手靠的是四道安全闸，不是默认就动手。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

from .cost import (
    ALERT_COST_MOM_GROWTH,
    ALERT_NAS_USAGE_RATIO,
    CostModel,
    aggregate_cost_daily,
    budget_alerts,
)
from .decision import (
    ActionType,
    Decision,
    NasContext,
    TrainingContext,
    evict_status_after,
    scan,
)
from .policy import PIPELINE_STEPS, SAFETY_GATES, EvictStatus
from .records import CostDailyRow, LifecycleRecord
from .repository import LifecycleRepository
from .tiers import ARCHIVE_RESTORE_SLA_HOURS, LifecycleStage, StorageMedia

__all__ = [
    "StorageExecutor",
    "NoopExecutor",
    "GovernancePlan",
    "RehearsalReport",
    "ExecutionResult",
    "GovernanceRun",
    "LARGE_BATCH_FILE_THRESHOLD",
    "LARGE_BATCH_TB_THRESHOLD",
    "EXECUTE_RATE_LIMIT_PER_SEC",
    "preheat_hit_rate",
]

_log = logging.getLogger(__name__)


#: ⚠️ 原文未明确，本项目设计：原文第五章②只说「大批量操作需人工确认后放行」，
#: 没给「大批量」的数值定义。本项目取 10000 个文件或 10 TB 任一超标即判为大批量。
#: 依据是月末复盘口径——当月治理释放 210TB，摊到 30 天约 7TB/天，
#: 10TB 定在日常量之上、异常量之下，既不会天天弹人工确认，也拦得住误判导致的大扫除。
LARGE_BATCH_FILE_THRESHOLD: int = 10_000
LARGE_BATCH_TB_THRESHOLD: float = 10.0

#: ⚠️ 原文未明确，本项目设计：原文第五章③写「限速执行，失败可断点续做」但未给速率。
#: 默认每秒 50 个对象操作，可按云厂商 OSS 生命周期 API 的配额调整。
EXECUTE_RATE_LIMIT_PER_SEC: int = 50


# --------------------------------------------------------------------------- 执行器


class StorageExecutor(Protocol):
    """存储执行服务接口（原文第五章③：调用 OSS 生命周期 API / NAS 清理任务）。

    真实实现去调云厂商 SDK；本模块只负责编排与安全闸，不绑定任何一朵云。
    """

    def tier_down(self, record: LifecycleRecord, target: StorageMedia) -> bool:
        """OSS 内部降冷（标准 → 低频 → 归档）。返回是否成功。"""
        ...

    def evict_nas(self, record: LifecycleRecord) -> bool:
        """清除 NAS 副本（淘汰 ≠ 删除，OSS 事实源保持不动）。"""
        ...

    def preheat(self, record: LifecycleRecord) -> bool:
        """把 OSS 对象预热到 NAS。"""
        ...

    def restore(self, record: LifecycleRecord) -> bool:
        """归档取回（标准恢复 ≤ 4 小时）。"""
        ...

    def delete(self, record: LifecycleRecord) -> bool:
        """删除对象。只有过了删除三重确认的记录才会走到这里。"""
        ...


class NoopExecutor:
    """空执行器：只记日志不动数据，用于 dry-run 与单测。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _record(self, op: str, record: LifecycleRecord) -> bool:
        self.calls.append((op, record.data_id, record.file_path))
        _log.debug("dry-run %s %s %s", op, record.data_id, record.file_path)
        return True

    def tier_down(self, record: LifecycleRecord, target: StorageMedia) -> bool:
        return self._record(f"tier_down->{target.value}", record)

    def evict_nas(self, record: LifecycleRecord) -> bool:
        return self._record("evict_nas", record)

    def preheat(self, record: LifecycleRecord) -> bool:
        return self._record("preheat", record)

    def restore(self, record: LifecycleRecord) -> bool:
        return self._record("restore", record)

    def delete(self, record: LifecycleRecord) -> bool:
        return self._record("delete", record)


# --------------------------------------------------------------------------- 步骤产物


@dataclass(slots=True)
class GovernancePlan:
    """① 扫描决策的产出：降冷 / 淘汰 / 删除候选清单。"""

    stat_date: date
    decisions: list[Decision]
    #: 被跳过的脏快照 ``(记录, 原因)``。scan 默认跳过单条脏数据以免整轮治理停摆，
    #: 但跳过必须可见——这些记录当天既没被降冷也没被淘汰，属于治理盲区。
    skipped: tuple[tuple[LifecycleRecord, str], ...] = ()

    @property
    def skipped_ratio(self) -> float:
        """脏快照占比。持续偏高说明上游写入有系统性问题，该去修源头而不是继续跳过。"""
        total = len(self.decisions) + len(self.skipped)
        return len(self.skipped) / total if total else 0.0

    def by_action(self, action: ActionType) -> list[Decision]:
        return [d for d in self.decisions if d.action is action]

    @property
    def actionable(self) -> list[Decision]:
        """需要真的动数据的决策。"""
        return [d for d in self.decisions if d.is_actionable]

    @property
    def blocked(self) -> list[Decision]:
        """被安全闸拦下的决策——必须进告警，不能悄悄吞掉。"""
        return self.by_action(ActionType.BLOCKED)

    def volume_tb(self, action: ActionType) -> float:
        return sum(d.volume_tb for d in self.by_action(action))


@dataclass(slots=True)
class RehearsalReport:
    """② 演练审计的产出：执行预览（文件清单 / 容量 / 成本变化）。"""

    plan: GovernancePlan
    file_count: int
    total_volume_tb: float
    cost_before_daily_yuan: float
    cost_after_daily_yuan: float
    requires_human_approval: bool
    approved: bool = False
    preview: list[dict[str, object]] = field(default_factory=list)

    @property
    def daily_saving_yuan(self) -> float:
        """本轮治理的当日成本变化（正值 = 省钱）。"""
        return self.cost_before_daily_yuan - self.cost_after_daily_yuan

    @property
    def released(self) -> bool:
        """是否可以放行执行：不需要人工确认，或已拿到人工确认。"""
        return (not self.requires_human_approval) or self.approved


@dataclass(slots=True)
class ExecutionResult:
    """③ 执行的产出。"""

    succeeded: list[Decision] = field(default_factory=list)
    failed: list[tuple[Decision, str]] = field(default_factory=list)
    skipped: list[Decision] = field(default_factory=list)
    #: 执行后的新快照，供回写步写回明细表
    updated_records: list[LifecycleRecord] = field(default_factory=list)
    #: 断点续做的游标：已处理到第几条（原文第五章③「失败可断点续做」）
    cursor: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


# --------------------------------------------------------------------------- 编排


class GovernanceRun:
    """一次日级治理调度。五步各自独立，串起来就是原文第五章的闭环。"""

    def __init__(
        self,
        repository: LifecycleRepository,
        *,
        executor: StorageExecutor | None = None,
        model: CostModel | None = None,
        approve_hook: Callable[[RehearsalReport], bool] | None = None,
    ) -> None:
        """
        :param repository: 两张表的读写实现。
        :param executor: 存储执行服务；默认 NoopExecutor（dry-run）。
        :param model: 成本模型；默认原文示例单价。
        :param approve_hook: 大批量操作的人工确认回调，返回 True 表示放行。
            默认 None —— 即「无人确认」，大批量一律不放行，这是保守侧。
        """
        self.repo = repository
        self.executor: StorageExecutor = executor or NoopExecutor()
        self.model = model or CostModel()
        self.approve_hook = approve_hook
        self.audit_log: list[dict[str, object]] = []

    # ---- ① 扫描决策 ----

    def step1_scan(
        self,
        *,
        training: TrainingContext,
        nas: NasContext | None = None,
        stat_date: date | None = None,
        limit: int | None = None,
    ) -> GovernancePlan:
        """① 扫描决策（执行者：StarRocks 定时任务 T+1）。

        原文：「扫描生命周期状态表，按规则逐条计算，产出降冷 / 淘汰 / 删除候选清单」。
        """
        records = self.repo.load_records(limit=limit)
        # 接住被跳过的脏快照：scan 默认 skip_invalid=True，不接就等于当天少治理了
        # 若干条却无人知晓——「脏数据由告警步捞出」要成立，这里必须留下入口。
        skipped: list[tuple[LifecycleRecord, str]] = []
        decisions = scan(records, training=training, nas=nas, skipped=skipped)
        plan = GovernancePlan(
            stat_date=stat_date or training.now.date(),
            decisions=decisions,
            skipped=tuple(skipped),
        )
        if skipped:
            _log.warning(
                "① 扫描跳过 %d 条脏快照（占比 %.2f%%），已计入 plan.skipped 供告警步消费；样例：%s",
                len(skipped),
                100.0 * len(skipped) / max(len(records), 1),
                "; ".join(f"{r.pk}: {why}" for r, why in skipped[:3]),
            )
        _log.info(
            "① 扫描决策完成：%d 条快照 → 降冷 %d / 淘汰 %d / 删除 %d / 预热 %d / 取回 %d / 拦截 %d",
            len(records),
            len(plan.by_action(ActionType.TIER_DOWN)),
            len(plan.by_action(ActionType.EVICT)),
            len(plan.by_action(ActionType.DELETE)),
            len(plan.by_action(ActionType.PREHEAT)),
            len(plan.by_action(ActionType.RESTORE)),
            len(plan.blocked),
        )
        return plan

    # ---- ② 演练审计 ----

    def step2_rehearse(self, plan: GovernancePlan) -> RehearsalReport:
        """② 演练审计（执行者：治理演练服务）。

        原文：「生成执行预览（文件清单 / 容量 / 成本变化），大批量操作需人工确认后放行」。
        大批量的数值口径见 ``LARGE_BATCH_FILE_THRESHOLD`` / ``LARGE_BATCH_TB_THRESHOLD``
        的 ⚠️ 说明。
        """
        actionable = plan.actionable
        before = sum(
            self.model.daily_cost(d.record.storage_media, d.record.size_gb) for d in actionable
        )
        after = 0.0
        for d in actionable:
            if d.action is ActionType.DELETE:
                continue  # 删掉了就不再计费
            media = d.target_media or d.record.storage_media
            after += self.model.daily_cost(media, d.record.size_gb)

        volume = sum(d.volume_tb for d in actionable)
        need_approval = (
            len(actionable) > LARGE_BATCH_FILE_THRESHOLD or volume > LARGE_BATCH_TB_THRESHOLD
        )
        report = RehearsalReport(
            plan=plan,
            file_count=len(actionable),
            total_volume_tb=volume,
            cost_before_daily_yuan=before,
            cost_after_daily_yuan=after,
            requires_human_approval=need_approval,
            preview=[d.to_audit_row() for d in actionable],
        )
        if need_approval and self.approve_hook is not None:
            report.approved = bool(self.approve_hook(report))
        _log.info(
            "② 演练审计：%d 个文件 / %.4f TB，日成本 ¥%.2f → ¥%.2f（省 ¥%.2f）；大批量=%s，放行=%s",
            report.file_count,
            report.total_volume_tb,
            report.cost_before_daily_yuan,
            report.cost_after_daily_yuan,
            report.daily_saving_yuan,
            need_approval,
            report.released,
        )
        return report

    # ---- ③ 执行 ----

    def step3_execute(
        self,
        report: RehearsalReport,
        *,
        dry_run: bool = True,
        resume_from: int = 0,
        now: datetime | None = None,
        rate_limit_per_sec: int | None = None,
    ) -> ExecutionResult:
        """③ 执行（执行者：存储执行服务）。

        原文：「调用 OSS 生命周期 API / NAS 清理任务，限速执行，失败可断点续做」。

        限速是真限速：每次调用执行器前按 ``rate_limit_per_sec`` 计算最小间隔并补齐。
        dry-run 不限速——空转不消耗云厂商配额，没必要陪着等。

        :param report: 演练审计报告；未放行则整轮跳过（返回全 skipped）。
        :param dry_run: True（默认）只走一遍流程不真动数据。
        :param resume_from: 断点续做的起始下标，接上次 ``ExecutionResult.cursor``。
        :param now: 本轮时点，写进动作时刻字段（stage_entered_at / preheat_time /
            tier_down_time），并作为取回动作「重置访问计时」的新起点。
        :param rate_limit_per_sec: 每秒最多几次执行器调用，默认
            ``EXECUTE_RATE_LIMIT_PER_SEC``；传 0 或负数表示不限速。
        """
        result = ExecutionResult(cursor=resume_from)
        actionable = report.plan.actionable
        limit = EXECUTE_RATE_LIMIT_PER_SEC if rate_limit_per_sec is None else rate_limit_per_sec
        min_interval = 1.0 / limit if limit and limit > 0 else 0.0
        last_call = 0.0

        if not report.released:
            result.skipped = list(actionable)
            _log.warning("③ 执行跳过：大批量操作未获人工确认（%d 个文件）", len(actionable))
            return result

        dispatch: dict[ActionType, Callable[[Decision], bool]] = {
            ActionType.TIER_DOWN: lambda d: self.executor.tier_down(
                d.record, d.target_media or StorageMedia.OSS_IA
            ),
            ActionType.EVICT: lambda d: self.executor.evict_nas(d.record),
            ActionType.PREHEAT: lambda d: self.executor.preheat(d.record),
            ActionType.RESTORE: lambda d: self.executor.restore(d.record),
            ActionType.DELETE: lambda d: self.executor.delete(d.record),
        }

        for idx in range(resume_from, len(actionable)):
            decision = actionable[idx]
            result.cursor = idx
            try:
                if dry_run:
                    ok = True
                else:
                    if min_interval:  # 限速执行：补齐两次调用之间的最小间隔
                        wait = min_interval - (time.monotonic() - last_call)
                        if wait > 0:
                            time.sleep(wait)
                    last_call = time.monotonic()
                    ok = dispatch[decision.action](decision)
            except Exception as exc:  # 单条失败不拖垮整轮，断点续做靠 cursor
                result.failed.append((decision, f"{type(exc).__name__}: {exc}"))
                _log.exception("③ 执行失败：%s %s", decision.action.value, decision.record.pk)
                continue
            if ok:
                result.succeeded.append(decision)
                result.updated_records.append(self._apply(decision, now=now))
            else:
                result.failed.append((decision, "执行器返回 False"))
            self.audit_log.append(decision.to_audit_row())  # 第三道闸：审计留痕

        result.cursor = len(actionable)
        _log.info(
            "③ 执行完成（dry_run=%s）：成功 %d / 失败 %d / 跳过 %d，限速 %s",
            dry_run,
            len(result.succeeded),
            len(result.failed),
            len(result.skipped),
            f"{limit} 次/秒" if min_interval else "不限速",
        )
        return result

    @staticmethod
    def _apply(decision: Decision, *, now: datetime | None = None) -> LifecycleRecord:
        """把决策作用到快照上，产出回写用的新状态。

        :param now: 本轮调度的时点。动作时刻要落进 ``stage_entered_at`` /
            ``preheat_time`` / ``tier_down_time`` 三个字段，缺了它们，
            「在当前分层待了多久」「预热了多久」下一轮就无从算起。
            不传则退回 ``datetime.now()``（只在脱离调度单独调用时才会走到）。
        """
        rec = decision.record
        moment = now or datetime.now()
        updated = LifecycleRecord(
            data_id=rec.data_id,
            file_path=rec.file_path,
            artifact_id=rec.artifact_id,
            project_code=rec.project_code,
            storage_media=decision.target_media or rec.storage_media,
            lifecycle_stage=decision.target_stage or rec.lifecycle_stage,
            data_type=rec.data_type,
            source_domain=rec.source_domain,
            file_size_bytes=rec.file_size_bytes,
            checksum_md5=rec.checksum_md5,
            create_time=rec.create_time,
            stage_entered_at=rec.stage_entered_at,
            last_access_time=rec.last_access_time,
            access_count_30d=rec.access_count_30d,
            lineage_ref_count=rec.lineage_ref_count,
            whitelist_flag=rec.whitelist_flag,
            expire_policy=rec.expire_policy,
            preheat_task_id=rec.preheat_task_id,
            preheat_time=rec.preheat_time,
            evict_status=rec.evict_status,
            tier_down_time=rec.tier_down_time,
        )
        if updated.lifecycle_stage is not rec.lifecycle_stage:
            updated.stage_entered_at = moment

        if decision.action is ActionType.DELETE:
            updated.lifecycle_stage = LifecycleStage.DELETED
        if decision.action is ActionType.EVICT:
            updated.evict_status = EvictStatus.DONE
        elif decision.action is ActionType.BLOCKED:
            updated.evict_status = evict_status_after(decision)
        if decision.action is ActionType.PREHEAT:
            updated.preheat_time = moment
        if decision.action is ActionType.TIER_DOWN:
            updated.tier_down_time = moment
        if decision.action is ActionType.RESTORE:
            # 第四道闸：「取回后自动回升温层并**重置访问计时**」。
            # 重置的是「连续无访问」这把尺子的起点，也就是 last_access_time——
            # 取回这一刻就是最近一次访问。只把 access_count_30d 清零是不够的：
            # 降冷判据用的是 days_since_access，last_access_time 不动的话，
            # 取回的第二天就会被判成「连续 N 天无访问」再降回归档，
            # 数据在归档与标准之间来回弹，每弹一次都是一笔取回费。
            updated.last_access_time = moment
            updated.access_count_30d = 0
        return updated

    # ---- ④ 回写 ----

    def step4_writeback(
        self,
        result: ExecutionResult,
        *,
        stat_date: date,
        nas_peak_usage: float = 0.0,
        preheat_hit_rate: float = 0.0,
        archive_restore_count: int | None = None,
    ) -> tuple[int, list[CostDailyRow]]:
        """④ 回写（执行者：存储执行服务）。

        原文：「结果回写生命周期状态表，更新成本日表」。

        :returns: ``(明细表写入行数, 成本日表行)``。
        """
        written = self.repo.upsert_records(result.updated_records)

        # 当日治理动作量按成本日表的五维主键归集
        volumes: dict[tuple[str, ...], dict[str, float]] = {}
        action_key = {
            ActionType.PREHEAT: "preheat",
            ActionType.EVICT: "evict",
            ActionType.TIER_DOWN: "tier_down",
            ActionType.DELETE: "delete",
        }
        for d in result.succeeded:
            key = action_key.get(d.action)
            if key is None:
                continue
            rec = d.record
            pk = (
                rec.storage_media.value,
                rec.lifecycle_stage.value,
                rec.data_type.code if rec.data_type else "",
                rec.source_domain,
            )
            volumes.setdefault(pk, {})
            volumes[pk][key] = volumes[pk].get(key, 0.0) + d.volume_tb

        restores = (
            archive_restore_count
            if archive_restore_count is not None
            else sum(1 for d in result.succeeded if d.action is ActionType.RESTORE)
        )
        # 环比要拿昨天同一组五维格子的成本来比（原文第六章告警线一：环比 > 10%）。
        # 读不到昨天（首日 / 仓储不支持）就让环比留 0，写 0 比写假值诚实。
        try:
            previous = self.repo.read_cost_daily(stat_date - timedelta(days=1))
        except Exception:  # pragma: no cover - 仓储各自的异常类型不统一
            _log.warning("④ 回写：读不到昨日成本日表，成本环比本轮留 0")
            previous = []
        rows = aggregate_cost_daily(
            self.repo.load_records(),
            stat_date=stat_date,
            model=self.model,
            action_volumes=volumes,
            nas_peak_usage=nas_peak_usage,
            preheat_hit_rate=preheat_hit_rate,
            archive_restore_count=restores,
            previous_day=previous,
        )
        self.repo.write_cost_daily(rows)
        _log.info("④ 回写：明细 %d 行，成本日表 %d 行", written, len(rows))
        return (written, rows)

    # ---- ⑤ 告警 ----

    def step5_alert(
        self,
        result: ExecutionResult,
        plan: GovernancePlan,
        *,
        cost_mom_growth: float = 0.0,
        nas_usage_ratio: float = 0.0,
        sustained: bool = True,
    ) -> list[dict[str, object]]:
        """⑤ 告警（执行者：告警服务 → 数据平台值班）。

        原文：「执行失败 / checksum 不一致 / 成本异动告警；结论反哺治理规则调优」。
        成本异动的两条线见 ``cost.budget_alerts``：
        环比增长 > 10%、NAS 使用率持续 > 80%。
        """
        alerts: list[dict[str, object]] = []

        if result.failed:
            alerts.append(
                {
                    "code": "execute_failed",
                    "level": "error",
                    "message": f"存储执行失败 {len(result.failed)} 条，可从 cursor={result.cursor} 断点续做",
                    "samples": [
                        {"pk": list(d.record.pk), "error": err} for d, err in result.failed[:10]
                    ],
                }
            )

        checksum_blocked = [d for d in plan.blocked if "淘汰校验" in d.blocked_by]
        if checksum_blocked:
            alerts.append(
                {
                    "code": "checksum_mismatch",
                    "level": "error",
                    "message": (
                        f"checksum 不一致 / 未校验 {len(checksum_blocked)} 条，"
                        f"已按铁律保留 NAS 副本（淘汰 ≠ 删除）"
                    ),
                    "samples": [list(d.record.pk) for d in checksum_blocked[:10]],
                }
            )

        delete_blocked = [d for d in plan.blocked if "删除三重确认" in d.blocked_by]
        if delete_blocked:
            alerts.append(
                {
                    "code": "delete_blocked",
                    "level": "info",
                    "message": (
                        f"删除三重确认拦截 {len(delete_blocked)} 条：保留期满但仍有血缘引用或白名单，"
                        f"继续留存——血缘定生死，训练永远可复现"
                    ),
                    "samples": [list(d.record.pk) for d in delete_blocked[:10]],
                }
            )

        alerts.extend(
            budget_alerts(
                cost_mom_growth=cost_mom_growth,
                nas_usage_ratio=nas_usage_ratio,
                sustained=sustained,
            )
        )
        for a in alerts:
            _log.warning("⑤ 告警 [%s] %s", a.get("code"), a.get("message"))
        return alerts

    # ---- 串起来 ----

    def run_daily(
        self,
        *,
        training: TrainingContext,
        nas: NasContext | None = None,
        stat_date: date | None = None,
        dry_run: bool = True,
        cost_mom_growth: float = 0.0,
        preheat_hit_rate: float = 0.0,
    ) -> dict[str, object]:
        """跑完整的一天：五步闭环。

        :param dry_run: 默认 True。生产放开前请先看演练报告——
            原文第五章②的「演练审计」就是为这一刻准备的。
        :returns: 五步各自的产出摘要。
        """
        nas_ctx = nas or NasContext()
        the_date = stat_date or training.now.date()

        plan = self.step1_scan(training=training, nas=nas_ctx, stat_date=the_date)
        report = self.step2_rehearse(plan)
        result = self.step3_execute(report, dry_run=dry_run, now=training.now)
        written, rows = self.step4_writeback(
            result,
            stat_date=the_date,
            nas_peak_usage=nas_ctx.usage_ratio,
            preheat_hit_rate=preheat_hit_rate,
        )
        alerts = self.step5_alert(
            result,
            plan,
            cost_mom_growth=cost_mom_growth,
            nas_usage_ratio=nas_ctx.usage_ratio,
        )
        return {
            "stat_date": the_date,
            "dry_run": dry_run,
            "steps": [s[0] for s in PIPELINE_STEPS],
            "safety_gates": [g[0] for g in SAFETY_GATES],
            "scanned": len(plan.decisions),
            "actionable": len(plan.actionable),
            "blocked": len(plan.blocked),
            "succeeded": len(result.succeeded),
            "failed": len(result.failed),
            "records_written": written,
            "cost_rows": len(rows),
            "daily_saving_yuan": round(report.daily_saving_yuan, 2),
            # 原文第六章看板指标「成本节省额 = 无治理基线成本 − 实际成本」
            "cost_saved_yuan": round(sum(r.saved_cost_yuan for r in rows), 2),
            "alerts": alerts,
            "archive_restore_sla_hours": ARCHIVE_RESTORE_SLA_HOURS,
            "alert_thresholds": {
                "cost_mom_growth": ALERT_COST_MOM_GROWTH,
                "nas_usage_ratio": ALERT_NAS_USAGE_RATIO,
            },
        }


def preheat_hit_rate(hits: int, requests: int) -> float:
    """预热命中率 = 训练预热命中 / 总预热请求（原文第六章看板口径）。

    :raises ValueError: 请求数为负，或命中数大于请求数。
    """
    if requests < 0 or hits < 0:
        raise ValueError(f"命中/请求数不能为负: hits={hits}, requests={requests}")
    if hits > requests:
        raise ValueError(f"命中数 {hits} 不能大于总请求数 {requests}")
    return hits / requests if requests else 0.0
