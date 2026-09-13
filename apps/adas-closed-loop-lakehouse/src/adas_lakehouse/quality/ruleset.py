"""规则中心：规则注册、按表检索、灰度发布与误杀率统计。

原文第六章：「门禁规则统一注册于质量监控服务，支持两项关键能力——
按表灰度发布新规则（新规则先在单表小流量试运行，监控误杀率，确认后再全量生效）；
异常统计反哺规则迭代」。

灰度语义（本模块实现）：

    OFF   规则完全不跑
    GREY  只在 grey_tables 指定的单表上、按 sample_ratio 小流量试运行；
          命中记为「影子命中」（RuleHit.shadow=True），**不参与处置**，
          只进误杀率统计——灰度期误杀不能真的把数据挡在门外
    FULL  正常生效，命中即按 severity 处置

⚠️ 原文未明确，本项目设计：P0 合规/安全级规则禁止 OFF、禁止 GREY——
合规红线不接受「试运行」，见 :meth:`RuleCenter.set_rollout`。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum

from .rules import RuleScope, RuleSpec
from .severity import Channel, IssueLevel
from .thresholds import (
    GREY_MIN_SAMPLES_BEFORE_PROMOTION,
    GREY_MISFIRE_RATE_THRESHOLD,
    GREY_SAMPLE_RATIO_DEFAULT,
)

__all__ = [
    "RolloutMode",
    "Rollout",
    "RuleStats",
    "RuleCenter",
]


class RolloutMode(str, Enum):
    """规则发布状态。"""

    OFF = "off"
    GREY = "grey"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class Rollout:
    """一条规则的发布配置。

    grey_tables 为空且 mode=GREY 时，规则在任何表上都不生效——
    灰度必须显式指定「先在哪张表试」，这正是原文说的「按表灰度」。
    """

    mode: RolloutMode = RolloutMode.FULL
    grey_tables: frozenset[str] = frozenset()
    #: 灰度抽样比例。⚠️ 原文只说「小流量」，默认值见 thresholds.GREY_SAMPLE_RATIO_DEFAULT
    sample_ratio: float = GREY_SAMPLE_RATIO_DEFAULT

    def __post_init__(self) -> None:
        if not 0.0 <= self.sample_ratio <= 1.0:
            raise ValueError(f"灰度抽样比例必须落在 [0,1]，收到 {self.sample_ratio}")

    def active_on(self, table: str) -> bool:
        """该规则在这张表上是否参与检查（灰度只认 grey_tables）。"""
        if self.mode is RolloutMode.OFF:
            return False
        if self.mode is RolloutMode.GREY:
            return table in self.grey_tables
        return True

    def enforcing(self) -> bool:
        """命中是否参与处置。灰度期一律不处置。"""
        return self.mode is RolloutMode.FULL


@dataclass(slots=True)
class RuleStats:
    """单条规则的运行统计，灰度转正与「异常统计反哺规则迭代」都看它。"""

    rule_id: str
    evaluated: int = 0
    hits: int = 0
    shadow_hits: int = 0
    #: 人工复核后判定为「误杀」的命中数，由 RuleCenter.record_misfire 回写
    misfires: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.evaluated if self.evaluated else 0.0

    @property
    def misfire_rate(self) -> float:
        """误杀率 = 误杀数 / 命中数（含影子命中）。没有命中时为 0。"""
        total_hits = self.hits + self.shadow_hits
        return self.misfires / total_hits if total_hits else 0.0

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "rule_id": self.rule_id,
            "evaluated": self.evaluated,
            "hits": self.hits,
            "shadow_hits": self.shadow_hits,
            "misfires": self.misfires,
            "hit_rate": round(self.hit_rate, 6),
            "misfire_rate": round(self.misfire_rate, 6),
        }


def _stable_sample(rule_id: str, key: str) -> float:
    """把 (rule_id, 记录键) 稳定映射到 [0,1)。

    同一条记录对同一条灰度规则永远做出相同的抽样判定，重放时结论可复现——
    这对「复验重入湖」是必要的：不能第一次抽中、复验时没抽中。
    """
    digest = hashlib.md5(f"{rule_id}:{key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


class RuleCenter:
    """规则中心（原文的「质量监控服务」在本项目里的落地）。

    职责：
      · 规则注册与唯一性保证
      · 按「表 + 通道 + 作用域」检索规则（门禁热路径，带缓存）
      · 灰度发布状态机 OFF / GREY / FULL
      · 误杀率统计与转正门槛
    """

    def __init__(self, rules: Iterable[RuleSpec] = ()) -> None:
        self._rules: dict[str, RuleSpec] = {}
        self._rollouts: dict[str, Rollout] = {}
        self._stats: dict[str, RuleStats] = {}
        self._cache: dict[tuple[str, str, str], tuple[RuleSpec, ...]] = {}
        for rule in rules:
            self.register(rule)

    # ---- 注册 ----

    def register(self, rule: RuleSpec, rollout: Rollout | None = None) -> RuleSpec:
        """注册一条规则。rule_id 全局唯一，重复注册直接报错。

        P0 合规/安全级在**注册这一刻**就要满足「必须全量硬拦截」——
        :meth:`set_rollout` 早就拦着不许把 P0 改成 OFF/GREY，但注册入口过去可以直接
        塞一个 OFF 的 rollout（或用 ``enabled=False`` 的规则声明）绕过去，
        P0 硬拦截就成了一句空话。两个入口的判据现在是同一条。
        """
        if rule.rule_id in self._rules:
            raise ValueError(f"规则 ID 重复: {rule.rule_id}")
        effective = rollout or Rollout(mode=RolloutMode.FULL if rule.enabled else RolloutMode.OFF)
        if rule.issue_level is IssueLevel.P0 and effective.mode is not RolloutMode.FULL:
            raise ValueError(
                f"规则 {rule.rule_id} 是 P0 合规/安全级，必须全量硬拦截，"
                f"不允许以 {effective.mode.value} 状态注册"
                + ("（enabled=False 会注册成 off）" if rollout is None else "")
            )
        self._rules[rule.rule_id] = rule
        self._rollouts[rule.rule_id] = effective
        self._stats[rule.rule_id] = RuleStats(rule.rule_id)
        self._cache.clear()
        return rule

    def register_many(self, rules: Iterable[RuleSpec]) -> None:
        for rule in rules:
            self.register(rule)

    def replace(self, rule: RuleSpec, rollout: Rollout | None = None) -> RuleSpec:
        """覆盖已注册的同 ID 规则（YAML 覆盖内置默认值时用）。统计一并重置。"""
        self._rules.pop(rule.rule_id, None)
        self._rollouts.pop(rule.rule_id, None)
        self._stats.pop(rule.rule_id, None)
        self._cache.clear()
        return self.register(rule, rollout)

    def upsert(self, rule: RuleSpec, rollout: Rollout | None = None) -> RuleSpec:
        """有则覆盖、无则注册。"""
        if rule.rule_id in self._rules:
            return self.replace(rule, rollout)
        return self.register(rule, rollout)

    # ---- 检索 ----

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self) -> Iterator[RuleSpec]:
        return iter(self._rules.values())

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._rules

    def get(self, rule_id: str) -> RuleSpec:
        try:
            return self._rules[rule_id]
        except KeyError:
            raise KeyError(f"未注册的规则: {rule_id!r}") from None

    def all_rules(self) -> tuple[RuleSpec, ...]:
        return tuple(self._rules.values())

    def tables(self) -> tuple[str, ...]:
        """所有被规则覆盖的表（不含通配 ``*``）。"""
        return tuple(sorted({r.table for r in self._rules.values() if r.table != "*"}))

    def rules_for(
        self,
        table: str,
        channel: Channel | None = None,
        scope: RuleScope = RuleScope.RECORD,
    ) -> tuple[RuleSpec, ...]:
        """该表在该通道下需要执行的规则（已过滤掉 OFF 与未灰度到本表的规则）。

        结果按 rule_id 排序并缓存——门禁热路径上不该每条记录重算一遍。
        """
        key = (table, channel.value if channel else "", scope.value)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        selected = [
            r
            for r in self._rules.values()
            if r.scope is scope
            and r.applies_to(table, channel)
            and self._rollouts[r.rule_id].active_on(table)
        ]
        selected.sort(key=lambda r: (r.issue_level.value, r.rule_id))
        result = tuple(selected)
        self._cache[key] = result
        return result

    # ---- 灰度 ----

    def rollout_of(self, rule_id: str) -> Rollout:
        return self._rollouts[self.get(rule_id).rule_id]

    def set_rollout(self, rule_id: str, rollout: Rollout) -> Rollout:
        """设置发布状态。

        P0 合规/安全级规则只允许 FULL——⚠️ 原文未明确，本项目设计：
        合规红线不接受关闭或试运行，否则「P0 硬拦截」就成了一句空话。
        """
        rule = self.get(rule_id)
        if rule.issue_level is IssueLevel.P0 and rollout.mode is not RolloutMode.FULL:
            raise ValueError(
                f"规则 {rule_id} 是 P0 合规/安全级，必须全量硬拦截，"
                f"不允许设置为 {rollout.mode.value}"
            )
        self._rollouts[rule_id] = rollout
        self._cache.clear()
        return rollout

    def start_grey(
        self, rule_id: str, table: str, sample_ratio: float = GREY_SAMPLE_RATIO_DEFAULT
    ) -> Rollout:
        """按表灰度：新规则先在单表小流量试运行。"""
        return self.set_rollout(
            rule_id,
            Rollout(
                mode=RolloutMode.GREY, grey_tables=frozenset({table}), sample_ratio=sample_ratio
            ),
        )

    def promote(self, rule_id: str, *, force: bool = False) -> Rollout:
        """灰度转全量。误杀率或样本量不达标时拒绝转正（force=True 可强推）。

        门槛（⚠️ 原文未明确，本项目设计，见 thresholds）：
          · 至少 GREY_MIN_SAMPLES_BEFORE_PROMOTION 条样本
          · 误杀率 ≤ GREY_MISFIRE_RATE_THRESHOLD
        """
        stats = self._stats[self.get(rule_id).rule_id]
        if not force:
            if stats.evaluated < GREY_MIN_SAMPLES_BEFORE_PROMOTION:
                raise ValueError(
                    f"规则 {rule_id} 灰度样本不足: {stats.evaluated} < "
                    f"{GREY_MIN_SAMPLES_BEFORE_PROMOTION}"
                )
            if stats.misfire_rate > GREY_MISFIRE_RATE_THRESHOLD:
                raise ValueError(
                    f"规则 {rule_id} 灰度误杀率 {stats.misfire_rate:.4f} 高于门槛 "
                    f"{GREY_MISFIRE_RATE_THRESHOLD}，不允许全量发布"
                )
        return self.set_rollout(rule_id, Rollout(mode=RolloutMode.FULL))

    def disable(self, rule_id: str) -> Rollout:
        """下线一条规则（P0 不可下线）。"""
        return self.set_rollout(rule_id, Rollout(mode=RolloutMode.OFF))

    def should_enforce(self, rule: RuleSpec, table: str, record_key: str) -> tuple[bool, bool]:
        """返回 (是否执行这条规则, 命中是否参与处置)。

        灰度规则按 record_key 做稳定抽样，保证同一条记录的判定可复现。
        """
        rollout = self._rollouts[rule.rule_id]
        if not rollout.active_on(table):
            return False, False
        if rollout.mode is RolloutMode.GREY:
            if _stable_sample(rule.rule_id, record_key) >= rollout.sample_ratio:
                return False, False
            return True, False
        return True, rollout.enforcing()

    # ---- 统计 ----

    def note_evaluated(self, rule_id: str, n: int = 1) -> None:
        self._stats[rule_id].evaluated += n

    def note_hit(self, rule_id: str, *, shadow: bool = False, n: int = 1) -> None:
        stats = self._stats[rule_id]
        if shadow:
            stats.shadow_hits += n
        else:
            stats.hits += n

    def record_misfire(self, rule_id: str, n: int = 1) -> RuleStats:
        """人工复核判定为误杀时回写。误杀率是灰度转正的唯一硬门槛。"""
        stats = self._stats[self.get(rule_id).rule_id]
        stats.misfires += n
        return stats

    def stats_of(self, rule_id: str) -> RuleStats:
        return self._stats[self.get(rule_id).rule_id]

    def stats_snapshot(self) -> dict[str, dict[str, float | int | str]]:
        """全量统计快照，进监控大屏与「异常统计反哺规则迭代」。"""
        return {rid: st.to_dict() for rid, st in self._stats.items()}

    def describe(self) -> list[dict[str, object]]:
        """规则清单（含发布状态），用于数据管理平台展示与审计。"""
        out: list[dict[str, object]] = []
        for rid, rule in self._rules.items():
            rollout = self._rollouts[rid]
            out.append(
                {
                    "rule_id": rid,
                    "table": rule.table,
                    "field": rule.field,
                    "check": rule.check.value,
                    "severity": rule.severity.value,
                    "disposition": rule.disposition_hint,
                    "dimension": rule.dimension.name_cn,
                    "issue_level": rule.issue_level.value,
                    "channel": rule.channel.value,
                    "scope": rule.scope.value,
                    "quality_layer": rule.quality_layer.key if rule.quality_layer else None,
                    "rollout": rollout.mode.value,
                    "grey_tables": sorted(rollout.grey_tables),
                    "sample_ratio": rollout.sample_ratio,
                    "repair_action": rule.repair_action.value,
                    "source": rule.source,
                }
            )
        out.sort(key=lambda d: (str(d["table"]), str(d["rule_id"])))
        return out

    # ---- YAML 三级结构 ----

    def to_yaml_tree(self) -> dict[str, dict[str, list[dict[str, object]]]]:
        """导出成原文的三级结构：检查类型 → 表 → 字段级规则列表。"""
        tree: dict[str, dict[str, list[dict[str, object]]]] = {}
        for rule in self._rules.values():
            tree.setdefault(rule.check.value, {}).setdefault(rule.table, []).append(rule.to_dict())
        for tables in tree.values():
            for rule_list in tables.values():
                rule_list.sort(key=lambda d: str(d["rule_id"]))
        return dict(sorted(tree.items()))

    @classmethod
    def from_yaml_tree(
        cls, tree: Mapping[str, Mapping[str, list[Mapping[str, object]]]]
    ) -> RuleCenter:
        """从三级结构还原规则中心。"""
        center = cls()
        for check, tables in tree.items():
            for table, rule_list in tables.items():
                for raw in rule_list:
                    center.register(RuleSpec.from_dict(raw, table=table, check=check))
        return center


@dataclass(slots=True)
class _RandomSampler:
    """随机抽样器。仅在没有记录键可用时兜底，默认不使用（稳定抽样优先）。"""

    seed: int | None = None
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def __call__(self) -> float:
        return self._rng.random()
