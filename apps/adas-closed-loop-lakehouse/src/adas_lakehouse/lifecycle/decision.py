"""扫描决策引擎：把「快照」算成「动作」。

原文第四章的三条可复用经验里，第一条就是本模块的全部职责：

    快照即决策——时间线上每个节点对应生命周期表的一次状态变更，治理服务只需扫表计算。

第二条是本模块的红线：

    血缘定生死——保留期满但引用数 > 0，三重确认把它拦在删除之外，训练永远可复现。

决策优先级（⚠️ 原文未明确先后，本项目按「越不可逆越靠后、越保护越靠前」定序）：

  1. 白名单豁免 —— 打了白名单标签的一律 HOLD（原文：不参与自动淘汰）
  2. 归档取回 —— 归档态又被访问 → RESTORE，回升温层并重置访问计时
  3. NAS 淘汰 —— 四条淘汰场景，只清副本不删事实源
  4. 删除 —— 三重确认全过才允许
  5. 降冷 —— 按保留期表逐级下沉
  6. HOLD —— 什么都不用做
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .policy import (
    COLD_TO_ARCHIVE_NO_ACCESS_DAYS,
    DEEP_ARCHIVE_NO_ACCESS_DAYS,
    DELETE_TRIPLE_CONFIRM,
    EVICT_RULES,
    LINEAGE_BUMP_FLOOR_STAGE,
    LINEAGE_BUMP_GRACE_DAYS,
    LINEAGE_BUMP_TIERS,
    NAS_CHECKPOINT_KEEP_VERSIONS,
    NAS_LRU_NO_ACCESS_DAYS,
    NAS_TRAINING_DONE_BUFFER_DAYS,
    NAS_WATERMARK_USAGE,
    EvictScenario,
    EvictStatus,
    media_for_stage,
    retention_for,
    target_stage_by_age,
)
from .records import LifecycleRecord
from .tiers import (
    ARCHIVE_RESTORE_SLA_HOURS,
    LifecycleStage,
    StorageMedia,
    colder_than,
    one_tier_warmer,
)

__all__ = [
    "ActionType",
    "Decision",
    "TrainingContext",
    "NasContext",
    "decide",
    "scan",
    "lru_evict_order",
    "delete_triple_confirm",
    "checksum_gate",
    "evict_status_after",
]


_LOG = logging.getLogger(__name__)


class ActionType(str, Enum):
    """治理动作。对应原文第五章①「产出降冷 / 淘汰 / 删除候选清单」，
    外加预热与取回两个反方向动作。"""

    HOLD = "hold"  # 维持现状
    PREHEAT = "preheat"  # 预热上 NAS（训练任务创建触发）
    EVICT = "evict"  # 淘汰 NAS 副本（淘汰 ≠ 删除）
    TIER_DOWN = "tier_down"  # 降冷：standard → ia → archive
    RESTORE = "restore"  # 归档取回，回升温层
    DELETE = "delete"  # 删除（必须过三重确认）
    BLOCKED = "blocked"  # 规则算出要动，但被安全闸拦下


@dataclass(frozen=True, slots=True)
class Decision:
    """一条决策。扫描决策步的产出单元，也是演练审计步的预览行。"""

    record: LifecycleRecord
    action: ActionType
    target_stage: LifecycleStage | None
    target_media: StorageMedia | None
    reason: str
    #: 命中的规则名（原文规则的机读标识），用于审计留痕与规则调优归因
    rule: str = ""
    #: 本次动作涉及的数据量（TB），汇总进成本日表的 *_volume_tb
    volume_tb: float = 0.0
    #: 被拦截时记录未通过的闸门
    blocked_by: tuple[str, ...] = ()

    @property
    def is_actionable(self) -> bool:
        """是否需要存储执行服务真的去动数据。"""
        return self.action not in (ActionType.HOLD, ActionType.BLOCKED)

    def to_audit_row(self) -> dict[str, object]:
        """审计留痕行（第三道安全闸：所有流转 / 淘汰 / 删除操作全量登记）。"""
        return {
            "data_id": self.record.data_id,
            "file_path": self.record.file_path,
            "action": self.action.value,
            "from_media": self.record.storage_media.value,
            "from_stage": self.record.lifecycle_stage.value,
            "to_media": self.target_media.value if self.target_media else "",
            "to_stage": self.target_stage.value if self.target_stage else "",
            "rule": self.rule,
            "reason": self.reason,
            "volume_tb": round(self.volume_tb, 6),
            "blocked_by": ",".join(self.blocked_by),
        }


@dataclass(slots=True)
class TrainingContext:
    """决策所需的训练侧上下文（元信息驱动：这些值本身也来自湖仓表）。

    ⚠️ 原文未明确字段来源表，本项目设计：``active_preheat_data_ids`` 取自训练域的
    活跃任务表，``next_task_referenced`` 取自「副本是否被下一任务引用」的判断，
    ``checkpoint_rank`` 取自同一模型下 Checkpoint 版本的倒序序号（1 = 最新）。
    """

    now: datetime
    #: 关联活跃训练任务并已发起预热的 data_id 集合 → 进热层 H1
    active_preheat_data_ids: frozenset[str] = frozenset()
    #: 训练任务结束时间（按 data_id），用于算 7 天缓冲期
    training_finished_at: dict[str, datetime] = field(default_factory=dict)
    #: 副本是否被下一任务引用（被引用则不淘汰）
    next_task_referenced: frozenset[str] = frozenset()
    #: Checkpoint 版本倒序序号：1 = 最新。> N 的历史版本转存 OSS 归档
    checkpoint_rank: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass(slots=True)
class NasContext:
    """NAS 侧容量上下文。

    ``usage_ratio`` 就是成本日表里的 ``nas_peak_usage`` 口径，> 0.80 触发水位淘汰。
    """

    usage_ratio: float = 0.0
    #: 是否已完成 NAS 副本与 OSS 对象的 checksum 比对（第二道安全闸）
    checksum_verified: frozenset[tuple[str, str]] = frozenset()

    @property
    def over_watermark(self) -> bool:
        """NAS 使用率是否越过 80% 水位线（原文第三章 + 第六章告警线）。"""
        return self.usage_ratio > NAS_WATERMARK_USAGE


# --------------------------------------------------------------------------- 安全闸


def delete_triple_confirm(
    record: LifecycleRecord, *, now: datetime
) -> tuple[bool, tuple[str, ...]]:
    """删除三重确认（第一道安全闸）。

    原文第五章：「过保留期 + 血缘零引用 + 白名单校验，三者同时满足才允许删除」。
    第四章案例第 7 个快照点演示了它的价值：+365 天保留期满，但血缘引用 > 0，
    「删除三重确认拦截，继续留存」。

    :returns: ``(是否放行, 未通过的条件名元组)``；条件名取自
        ``policy.DELETE_TRIPLE_CONFIRM``。
    """
    failed: list[str] = []

    past_retention = False
    if record.data_type is not None:
        rule = retention_for(record.data_type)
        if rule.delete_after_days is not None:
            past_retention = record.days_since_create(now) >= rule.delete_after_days
    if not past_retention:
        failed.append(DELETE_TRIPLE_CONFIRM[0])

    if record.lineage_ref_count != 0:
        failed.append(DELETE_TRIPLE_CONFIRM[1])

    if record.whitelist_flag:
        failed.append(DELETE_TRIPLE_CONFIRM[2])

    return (not failed, tuple(failed))


def checksum_gate(record: LifecycleRecord, nas: NasContext) -> tuple[bool, str]:
    """淘汰校验（第二道安全闸）。

    原文第三章铁律：「淘汰 ≠ 删除。淘汰只清除 NAS 副本，OSS 始终是事实源；
    淘汰前必须校验 NAS 副本与 OSS 对象 checksum 一致，不一致则告警并保留副本」。

    :returns: ``(是否放行, 原因)``。
    """
    if not record.checksum_md5:
        return (False, "NAS 副本无 checksum_md5，无法与 OSS 对象比对")
    if record.pk not in nas.checksum_verified:
        return (False, "NAS 副本与 OSS 对象 checksum 未校验或不一致 → 告警并保留副本")
    return (True, "checksum 一致，可释放 NAS 副本")


# --------------------------------------------------------------------------- 决策


def _bump(stage: LifecycleStage) -> LifecycleStage:
    """血缘保护的提档动作：往热的方向退 ``LINEAGE_BUMP_TIERS`` 档，最热只到温层。

    上界 ``LINEAGE_BUMP_FLOOR_STAGE`` 是硬的——热层 H1 的进入条件是
    「数据集关联活跃训练任务并预热」，只能由预热动作产生。被引用的温层数据
    再怎么提档也不会自己跳上 NAS，否则分层档位就被当成介质档位用了。
    """
    bumped = stage
    for _ in range(LINEAGE_BUMP_TIERS):
        bumped = one_tier_warmer(bumped, floor=LINEAGE_BUMP_FLOOR_STAGE)
    return bumped


def _lineage_bumped_stage(
    record: LifecycleRecord, rule_stage: LifecycleStage, *, now: datetime
) -> tuple[LifecycleStage, str]:
    """血缘保护：被引用的数据自动提升一档保留（原文第三章）。

    提档不是永久的——提档宽限期（``LINEAGE_BUMP_GRACE_DAYS``，⚠️ 本项目从案例反推的
    30 天）过后，规则重新生效，数据继续下沉。案例 04-30 那一行「30 天无访问且
    **提档保留期满**，自动降冷」就是宽限期到点的表现。

    :returns: ``(提档后的目标分层, 提档说明)``；未触发提档时原样返回。
    """
    if record.lineage_ref_count <= 0:
        return (rule_stage, "")
    if record.data_type is None:
        bumped = _bump(rule_stage)
        return (
            bumped,
            f"血缘引用 {record.lineage_ref_count} 次 → 提升 {LINEAGE_BUMP_TIERS} 档保留",
        )

    rule = retention_for(record.data_type)
    age = record.days_since_create(now)
    grace_end = rule.standard_days + LINEAGE_BUMP_GRACE_DAYS
    if age < grace_end:
        bumped = _bump(rule_stage)
        return (
            bumped,
            f"血缘引用 {record.lineage_ref_count} 次 → 提升 {LINEAGE_BUMP_TIERS} 档保留"
            f"（提档宽限期 {LINEAGE_BUMP_GRACE_DAYS} 天，第 {age}/{grace_end} 天）",
        )
    return (rule_stage, f"提档保留期满（{grace_end} 天），规则恢复生效")


def _should_deep_archive(record: LifecycleRecord, *, now: datetime) -> bool:
    """归档级 C2 里，这份数据该落普通归档还是深度归档。

    原文第二章归档级 C2 的进入条件是「连续 180 天无访问**且过保留策略阈值**」，
    两个条件缺一不可：

    * 连续无访问天数 ≥ ``DEEP_ARCHIVE_NO_ACCESS_DAYS``（180）；
    * 过保留策略阈值——按数据类型的保留期表，已经走完归档段的下界
      （原始数据 90 天进归档、中间产物 180 天进归档…）。数据类型缺失时，
      以「创建天数 ≥ 180」作为保留策略阈值的兜底。

    深度归档是 0.05x 的最便宜档，取回也最慢，所以宁可判严不判松。
    """
    if record.days_since_access(now) < DEEP_ARCHIVE_NO_ACCESS_DAYS:
        return False
    if record.data_type is None:
        return record.days_since_create(now) >= DEEP_ARCHIVE_NO_ACCESS_DAYS
    rule = retention_for(record.data_type)
    threshold = rule.ia_until_days or rule.standard_days
    return record.days_since_create(now) >= threshold


def _nas_decision(
    record: LifecycleRecord,
    training: TrainingContext,
    nas: NasContext,
) -> Decision | None:
    """NAS 侧四条淘汰场景。返回 None 表示这条 NAS 副本本轮不动。"""
    now = training.now
    vol = record.size_tb

    # 场景四：白名单豁免——在 decide() 里已前置拦截，这里只是防御性再挡一次
    if record.whitelist_flag:
        return Decision(
            record,
            ActionType.HOLD,
            None,
            None,
            EVICT_RULES[EvictScenario.WHITELIST_EXEMPT].rule,
            rule=EvictScenario.WHITELIST_EXEMPT.value,
        )

    def _evict(target_stage: LifecycleStage, scenario: EvictScenario, why: str) -> Decision:
        ok, msg = checksum_gate(record, nas)
        target_media = media_for_stage(target_stage)
        if not ok:
            return Decision(
                record,
                ActionType.BLOCKED,
                target_stage,
                target_media,
                f"{why}；但 {msg}",
                rule=scenario.value,
                volume_tb=vol,
                blocked_by=("淘汰校验",),
            )
        return Decision(
            record,
            ActionType.EVICT,
            target_stage,
            target_media,
            f"{why}；{msg}",
            rule=scenario.value,
            volume_tb=vol,
        )

    # 场景二：Checkpoint 产物——NAS 仅保留最新 N 个版本（默认 3），历史版本转存 OSS 归档
    rank = training.checkpoint_rank.get(record.pk)
    if rank is not None and rank > NAS_CHECKPOINT_KEEP_VERSIONS:
        return _evict(
            LifecycleStage.ARCHIVE,
            EvictScenario.CHECKPOINT_ROTATE,
            f"Checkpoint 版本序号 {rank} > 保留版本数 {NAS_CHECKPOINT_KEEP_VERSIONS}"
            f"（原文默认 N=3），历史版本转存 OSS 归档",
        )

    # 场景一：训练任务完成后——任务已结束且副本未被下一任务引用，7 天缓冲期后淘汰
    finished = training.training_finished_at.get(record.data_id)
    if finished is not None and record.data_id not in training.next_task_referenced:
        buffered_days = (now - finished).days
        if buffered_days >= NAS_TRAINING_DONE_BUFFER_DAYS:
            return _evict(
                LifecycleStage.WARM,
                EvictScenario.TRAINING_DONE,
                f"训练任务已结束 {buffered_days} 天 ≥ {NAS_TRAINING_DONE_BUFFER_DAYS} 天缓冲期，"
                f"且副本未被下一任务引用",
            )
        return Decision(
            record,
            ActionType.HOLD,
            None,
            None,
            f"训练已结束但仍在 {NAS_TRAINING_DONE_BUFFER_DAYS} 天缓冲期内（第 {buffered_days} 天）",
            rule=EvictScenario.TRAINING_DONE.value,
        )

    # 场景三：容量水位——NAS 使用率 > 80% 触发，按 LRU 优先淘汰近 30 天无访问数据
    if nas.over_watermark and record.access_count_30d == 0:
        if record.data_id in training.active_preheat_data_ids:
            return Decision(
                record,
                ActionType.HOLD,
                None,
                None,
                "水位淘汰候选，但该副本关联活跃训练任务，保留",
                rule=EvictScenario.CAPACITY_WATERMARK.value,
            )
        return _evict(
            LifecycleStage.WARM,
            EvictScenario.CAPACITY_WATERMARK,
            f"NAS 使用率 {nas.usage_ratio:.0%} > {NAS_WATERMARK_USAGE:.0%} 水位线，"
            f"LRU 命中：近 {NAS_LRU_NO_ACCESS_DAYS} 天访问次数为 0",
        )

    return None


def decide(
    record: LifecycleRecord,
    *,
    training: TrainingContext,
    nas: NasContext | None = None,
) -> Decision:
    """对单条快照做决策。

    :param record: 生命周期状态表的一行快照。
    :param training: 训练侧上下文（含 ``now``，全批次共用同一时点，保证决策可复现）。
    :param nas: NAS 侧容量与 checksum 上下文；对 OSS 侧数据可省略。
    :returns: 一条 ``Decision``；不需要动的返回 ``ActionType.HOLD``。
    :raises ValueError: 快照自身不一致（介质与分层矛盾等），先修数据再决策。
    """
    problems = record.validate()
    if problems:
        raise ValueError(f"快照不一致，拒绝决策 {record.pk}: {'; '.join(problems)}")

    now = training.now
    nas_ctx = nas or NasContext()

    # ① 白名单豁免——原文第三章「活跃调试 / 在研迭代数据打白名单标签，不参与自动淘汰」；
    #    第三章 OSS 侧也写「合规留存与长期回归测试数据可打白名单标签跳过分层流转」。
    if record.whitelist_flag:
        return Decision(
            record,
            ActionType.HOLD,
            None,
            None,
            "白名单豁免：跳过分层流转，不参与自动淘汰",
            rule="whitelist",
        )

    # ② 预热：数据集关联活跃训练任务并预热 → 热层 H1
    if (
        record.data_id in training.active_preheat_data_ids
        and record.storage_media is not StorageMedia.NAS
    ):
        # 归档态不能直接预热上 NAS——归档对象要先走取回（标准恢复 ≤ 4 小时）才可读。
        # 原文第五章第四道闸把取回定义成「回升温层」的独立动作，预热是温层之后的事。
        if record.lifecycle_stage is LifecycleStage.ARCHIVE:
            return Decision(
                record,
                ActionType.RESTORE,
                LifecycleStage.WARM,
                StorageMedia.OSS_STANDARD,
                f"训练预热命中归档态数据：归档对象不可直读，先取回"
                f"（标准恢复 ≤ {ARCHIVE_RESTORE_SLA_HOURS} 小时）回升温层，下一轮再预热上 NAS",
                rule="archive_restore",
                volume_tb=record.size_tb,
            )
        return Decision(
            record,
            ActionType.PREHEAT,
            LifecycleStage.HOT,
            StorageMedia.NAS,
            "数据集关联活跃训练任务并预热 → 热 H1（CPFS/NAS）",
            rule="preheat",
            volume_tb=record.size_tb,
        )

    # ③ 归档取回：归档态又产生访问 → 标准恢复 ≤ 4 小时，取回后自动回升温层并重置访问计时
    if record.lifecycle_stage is LifecycleStage.ARCHIVE and record.access_count_30d > 0:
        return Decision(
            record,
            ActionType.RESTORE,
            LifecycleStage.WARM,
            StorageMedia.OSS_STANDARD,
            f"归档数据近 {NAS_LRU_NO_ACCESS_DAYS} 天有 {record.access_count_30d} 次访问 → 取回"
            f"（标准恢复 ≤ {ARCHIVE_RESTORE_SLA_HOURS} 小时），回升温层并重置访问计时",
            rule="archive_restore",
            volume_tb=record.size_tb,
        )

    # ④ NAS 侧四条淘汰场景
    if record.storage_media is StorageMedia.NAS:
        nas_decision = _nas_decision(record, training, nas_ctx)
        if nas_decision is not None:
            return nas_decision
        return Decision(
            record,
            ActionType.HOLD,
            None,
            None,
            "NAS 副本未命中任何淘汰场景，继续服务训练",
            rule="nas_hold",
        )

    # ⑤ OSS 侧：按保留期表算目标分层，再叠加血缘提档
    rule_stage = target_stage_by_age(
        record.data_type,
        days_since_create=record.days_since_create(now),
        days_since_access=record.days_since_access(now),
    )
    target, bump_note = _lineage_bumped_stage(record, rule_stage, now=now)

    # 冷 → 归档另有一条案例口径：连续 90 天无访问（见 policy 的 ⚠️ 说明）
    if (
        record.lifecycle_stage is LifecycleStage.COLD
        and target is LifecycleStage.COLD
        and record.days_since_access(now) >= COLD_TO_ARCHIVE_NO_ACCESS_DAYS
    ):
        target = LifecycleStage.ARCHIVE
        bump_note = f"连续 {COLD_TO_ARCHIVE_NO_ACCESS_DAYS} 天无访问 → 归档流转（案例口径）"

    # 归档 → 深度归档：原文第二章归档级 C2 给的是「OSS 归档 / 深度归档」两档介质，
    # 进入条件「连续 180 天无访问且过保留策略阈值」。分层不变（仍是 archive），
    # 变的是介质——这正是「分层档位」与「介质档位」两个维度各走各的地方。
    if target is LifecycleStage.ARCHIVE and _should_deep_archive(record, now=now):
        media = media_for_stage(target, deep_archive=True)
        why = (
            f"连续 {record.days_since_access(now)} 天无访问 ≥ "
            f"{DEEP_ARCHIVE_NO_ACCESS_DAYS} 天且过保留策略阈值 → 深度归档"
            f"（原文第二章归档级 C2 的第二档介质，0.05x）"
        )
        if record.storage_media is not media:
            return Decision(
                record,
                ActionType.TIER_DOWN,
                target,
                media,
                why,
                rule="deep_archive",
                volume_tb=record.size_tb,
            )

    if target is LifecycleStage.PENDING_DELETE:
        allowed, failed = delete_triple_confirm(record, now=now)
        if allowed:
            return Decision(
                record,
                ActionType.DELETE,
                LifecycleStage.PENDING_DELETE,
                None,
                f"删除三重确认全部通过（{'、'.join(DELETE_TRIPLE_CONFIRM)}）",
                rule="delete",
                volume_tb=record.size_tb,
            )
        return Decision(
            record,
            ActionType.BLOCKED,
            LifecycleStage.PENDING_DELETE,
            None,
            f"保留期满但删除三重确认未过：{'、'.join(failed)}；"
            f"血缘引用 {record.lineage_ref_count} → 继续留存，训练永远可复现",
            rule="delete",
            volume_tb=record.size_tb,
            blocked_by=("删除三重确认",),
        )

    if colder_than(target, record.lifecycle_stage):
        media = media_for_stage(target)
        why = f"规则目标分层 {target.value}（当前 {record.lifecycle_stage.value}）"
        if bump_note:
            why = f"{why}；{bump_note}"
        return Decision(
            record,
            ActionType.TIER_DOWN,
            target,
            media,
            why,
            rule="tier_down",
            volume_tb=record.size_tb,
        )

    hold_why = f"当前分层 {record.lifecycle_stage.value} 已符合规则目标 {target.value}"
    if bump_note:
        hold_why = f"{hold_why}；{bump_note}"
    return Decision(record, ActionType.HOLD, None, None, hold_why, rule="hold")


def scan(
    records: Iterable[LifecycleRecord],
    *,
    training: TrainingContext,
    nas: NasContext | None = None,
    skip_invalid: bool = True,
    skipped: list[tuple[LifecycleRecord, str]] | None = None,
) -> list[Decision]:
    """批量扫描决策，对应五步闭环的第 ① 步（StarRocks 定时任务 T+1）。

    原文第五章①：「扫描生命周期状态表，按规则逐条计算，产出降冷 / 淘汰 / 删除候选清单」。

    :param records: 生命周期状态表的快照流。
    :param training: 训练侧上下文，全批共用一个 ``now``。
    :param nas: NAS 侧上下文。
    :param skip_invalid: 脏快照是跳过（True，默认）还是中断整轮（False）。
        批量作业默认跳过——单条脏数据不该让当天治理停摆，脏数据由告警步捞出。
    :param skipped: 传入一个列表即可接住被跳过的 ``(记录, 原因)``，供告警步消费。
        不传也会打 WARNING 日志——跳过必须留痕，否则「由告警步捞出」就是空话。
    :returns: 决策列表。顺序与输入一致，唯一的例外是水位淘汰候选之间按 LRU 重排
        （见 ``lru_evict_order``）——原文写的是「按 LRU **优先**淘汰」，
        「优先」就是一个排序要求，不排序等于没实现。
    """
    out: list[Decision] = []
    for rec in records:
        try:
            out.append(decide(rec, training=training, nas=nas))
        except ValueError as exc:
            if not skip_invalid:
                raise
            # docstring 承诺「脏数据由告警步捞出」——那就必须留下可捞的痕迹。
            # 静默 pass 会让被跳过的记录彻底消失：当天治理少算了多少、少删了多少
            # 都无从得知，正是最难排查的那种失效。
            _LOG.warning("生命周期扫描跳过脏快照 %s: %s", rec.pk, exc)
            if skipped is not None:
                skipped.append((rec, str(exc)))
    return lru_evict_order(out)


def lru_evict_order(decisions: list[Decision]) -> list[Decision]:
    """把水位淘汰候选之间按 LRU 重排，其余决策保持原序。

    原文第三章容量水位那条规则：「NAS 使用率 > 80% 触发水位淘汰，
    **按 LRU 优先淘汰**近 30 天无访问数据」。水位淘汰的目的是尽快把使用率压回
    水位线以下，谁先被清掉是有讲究的：最久没被访问的先走（LRU 本义），
    同样久没访问的，先清体积大的——同样一次操作，腾出的空间更多。

    只动水位淘汰这一类，是因为其余动作（训练结束淘汰、Checkpoint 轮转、降冷、删除）
    的触发条件互相独立，没有先后之分，重排只会让审计对不上账。

    :param decisions: 一轮扫描的全部决策。
    :returns: 重排后的新列表（不修改入参）。
    """
    scenario = EvictScenario.CAPACITY_WATERMARK.value
    slots = [i for i, d in enumerate(decisions) if d.rule == scenario and d.is_actionable]
    if len(slots) < 2:
        return list(decisions)

    def _lru_key(d: Decision) -> tuple[float, float]:
        rec = d.record
        # 从未访问过的排在最前（最久没被碰）；体积大的先清，故取负值
        stamp = rec.last_access_time.timestamp() if rec.last_access_time else float("-inf")
        return (stamp, -rec.file_size_bytes)

    ordered = sorted((decisions[i] for i in slots), key=_lru_key)
    out = list(decisions)
    for slot, decision in zip(slots, ordered, strict=True):
        out[slot] = decision
    return out


def evict_status_after(decision: Decision) -> EvictStatus:
    """决策 → 回写到 ``evict_status`` 字段的取值（none / pending / done / skipped）。

    ⚠️ 原文未明确映射关系，本项目设计：扫描决策步只把淘汰候选标成 ``pending``，
    真正执行成功后由回写步改成 ``done``；被安全闸拦下的记 ``skipped``。
    """
    if decision.action is ActionType.EVICT:
        return EvictStatus.PENDING
    if decision.action is ActionType.BLOCKED:
        return EvictStatus.SKIPPED
    return EvictStatus.NONE
