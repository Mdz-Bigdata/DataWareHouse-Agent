"""高价值场景的评分与排序。

⚠️ 原文未明确，本项目设计：
    原文给了「为什么要排序」和「排序的结果被谁消费」，但**没有给出任何打分公式**。
    原文能拿到的只有三句话：

    · [S3-04] 一：「rule_priority 不只是排序字段——它直接决定 Embedding 与存储分级，
      高优先级规则命中的数据优先进入向量化队列」；
    · [S3-01] 五：「高价值数据（规则命中 / 事件抽帧 / VLM 标签）优先向量化，
      普通数据抽样处理——GPU 成本花在刀刃上」；
    · [S3-04] 四：规则是三级漏斗的第一层，「以几乎为零的边际成本扫完全量元数据，
      把「疑似高价值」的候选圈出来」，再交给 VLM 细筛。

    所以本模块的加权公式、权重取值、分档阈值、时间衰减半衰期全部是本项目设计，
    不要当成原文方案。唯一严格遵守原文的是**职责边界**：

        规则优先级决定「进哪个队列」（原文），评分只决定「队内怎么排」（本项目）。

    这条边界写进了 :func:`resolve_vectorize_policy`——评分再高也不会把一条 P3 规则
    的命中塞进优先向量化队列，否则就篡改了原文的分级语义。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ._sqlfmt import ident, literal
from .constants import EVENT_WINDOW_TOTAL_SEC
from .rules import (
    ConditionGroup,
    RuleDefinition,
    RuleType,
    SignalCondition,
    VectorizePolicy,
)

__all__ = [
    "ScoreWeights",
    "DEFAULT_WEIGHTS",
    "ValueTier",
    "TIER_THRESHOLDS",
    "FRESHNESS_HALF_LIFE_DAYS",
    "RARITY_FLOOR_TARGET",
    "ScoreBreakdown",
    "score_hit",
    "rarity_prior",
    "signal_severity",
    "type_bonus",
    "freshness",
    "classify_tier",
    "resolve_vectorize_policy",
    "rank_hits",
    "sql_score_expression",
    "sql_tier_expression",
    "event_window_seconds",
    "top_n",
]


# --------------------------------------------------------------------------- 权重


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """五因子权重，必须加总为 1.0。

    ⚠️ 原文未明确，本项目设计。权重的取舍思路（不是原文的，是本项目的）：

    * ``priority`` 最大（0.40）——原文明确 rule_priority 驱动下游，它必须主导排序；
    * ``rarity`` 次之（0.25）——挖掘的目的是补数据集缺口，越缺的场景越值钱；
    * ``severity`` 再次（0.20）——信号超阈越多，corner case 成色越足；
    * ``type_bonus``（0.10）——事件类与复合类规则的命中，下游动作更重（要补抽帧），
      给一点固定加成；
    * ``freshness``（0.05）——只做轻微加权，老数据并不因为老就没价值。
    """

    priority: float = 0.40
    rarity: float = 0.25
    severity: float = 0.20
    type_bonus: float = 0.10
    freshness: float = 0.05

    def __post_init__(self) -> None:
        total = self.priority + self.rarity + self.severity + self.type_bonus + self.freshness
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"评分权重必须加总为 1.0，当前 {total}")


DEFAULT_WEIGHTS = ScoreWeights()

#: 新鲜度半衰期，天。⚠️ 原文未明确，本项目设计：取 30 天，即一个月前的数据新鲜度因子折半。
FRESHNESS_HALF_LIFE_DAYS = 30.0

#: 稀缺度计算的目标量兜底值。⚠️ 原文未明确，本项目设计：
#: 规则没填 target_clip_count 时，用这个值当分母，避免除零，也避免「没填目标 = 无限稀缺」。
RARITY_FLOOR_TARGET = 1000


class ValueTier(str, Enum):
    """价值分档。⚠️ 原文未明确，本项目设计：四档 S/A/B/C。

    分档不改变向量化队列归属（那是 rule_priority 的职权），只用于：
    ADS 看板展示、人工复核抽样、以及交给下游 VLM 细筛时的候选排序。
    """

    S = "S"
    A = "A"
    B = "B"
    C = "C"


#: 分档阈值（含下界）。⚠️ 原文未明确，本项目设计。
TIER_THRESHOLDS: tuple[tuple[float, ValueTier], ...] = (
    (80.0, ValueTier.S),
    (60.0, ValueTier.A),
    (40.0, ValueTier.B),
    (0.0, ValueTier.C),
)


# --------------------------------------------------------------------------- 单因子


def rarity_prior(target_clip_count: int, existing_hit_count: int) -> float:
    """稀缺度：库内已有量离目标量还差多少，越差越稀缺。

    公式（⚠️ 本项目设计）::

        rarity = 1 - min(1, existing_hit_count / max(target, RARITY_FLOOR_TARGET))

    直接对应原文 [S3-01] 一的「挖掘双出口」——库内已经够了就不必再挖，
    差得越远越值得优先送进下游细筛，缺口部分则由 gaps 模块下发定向采集需求。

    Args:
        target_clip_count: 该场景的需求目标 clip 数。
        existing_hit_count: 库内已命中的 clip 数。

    Returns:
        [0, 1] 区间的稀缺度，1 表示完全没有。
    """
    denom = max(int(target_clip_count), RARITY_FLOOR_TARGET)
    ratio = max(0, int(existing_hit_count)) / denom
    return max(0.0, 1.0 - min(1.0, ratio))


def signal_severity(condition: SignalCondition | None, observed_value: float | None) -> float:
    """信号严重度：观测值超阈的相对幅度，归一到 [0, 1]。

    公式（⚠️ 本项目设计）::

        severity = clamp(|observed - threshold| / |threshold|, 0, 1)

    以原文唯一给出的阈值为例（[S3-04] 二：CAN 减速度 < -4m/s²）：
    实测 -4m/s² 恰好触线 → 0.0；-6m/s² → 0.5；-8m/s² 及以上 → 1.0（封顶）。

    Args:
        condition: 触发该命中的信号条件；非信号类规则传 None。
        observed_value: 实测信号值；拿不到时传 None。

    Returns:
        [0, 1]。缺少条件或观测值时返回 0.0——没有证据就不加分。
    """
    if condition is None or observed_value is None:
        return 0.0
    threshold = float(condition.threshold)
    if threshold == 0.0:
        return 0.0
    excess = abs(float(observed_value) - threshold) / abs(threshold)
    return max(0.0, min(1.0, excess))


def type_bonus(rule_type: RuleType) -> float:
    """规则种类加成。

    ⚠️ 原文未明确，本项目设计。依据是原文对各类规则「下游动作轻重」的描述：

    * 事件触发（1.0）——命中会异步触发补抽帧，对前 15 后 5 秒窗口加密采样
      （[S3-04] 三隐藏联动），下游动作最重，价值信号最强；
    * 多条件复合（0.9）——多条件叠加，误报率天然更低；
    * 车辆信号（0.8）、模型输出（0.7）——有物理/模型证据支撑；
    * 时空地理（0.5）、标签组合（0.4）——纯静态条件，覆盖面大但特异性弱。
    """
    return _TYPE_BONUS[rule_type]


_TYPE_BONUS: dict[RuleType, float] = {
    RuleType.EVENT_TRIGGER: 1.0,
    RuleType.COMPOSITE: 0.9,
    RuleType.VEHICLE_SIGNAL: 0.8,
    RuleType.MODEL_OUTPUT: 0.7,
    RuleType.SPATIOTEMPORAL: 0.5,
    RuleType.TAG_COMBINATION: 0.4,
}


def freshness(collected_at: datetime | None, *, now: datetime | None = None) -> float:
    """新鲜度：按半衰期指数衰减。

    公式（⚠️ 本项目设计）::

        freshness = 0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS)

    Args:
        collected_at: clip 采集时间；未知返回 0.5（不奖不罚的中位数）。
        now: 参考时刻，默认当前。

    Returns:
        (0, 1] 区间。
    """
    if collected_at is None:
        return 0.5
    ref = now or datetime.now()
    age_days = max(0.0, (ref - collected_at).total_seconds() / 86400.0)
    return 0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS)


# --------------------------------------------------------------------------- 综合评分


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """评分明细。分数落 dwd_mining_result_detail.value_score，明细用于解释与调参。"""

    value_score: float
    tier: ValueTier
    priority_component: float
    rarity_component: float
    severity_component: float
    type_component: float
    freshness_component: float
    weights: ScoreWeights = DEFAULT_WEIGHTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_score": round(self.value_score, 4),
            "value_tier": self.tier.value,
            "priority_component": round(self.priority_component, 4),
            "rarity_component": round(self.rarity_component, 4),
            "severity_component": round(self.severity_component, 4),
            "type_component": round(self.type_component, 4),
            "freshness_component": round(self.freshness_component, 4),
        }

    def explain(self) -> str:
        """一行可读解释，写进执行日志便于事后复盘打分是否合理。"""
        return (
            f"score={self.value_score:.2f}({self.tier.value}) = "
            f"{self.weights.priority:.2f}*prio {self.priority_component:.3f} + "
            f"{self.weights.rarity:.2f}*rarity {self.rarity_component:.3f} + "
            f"{self.weights.severity:.2f}*sev {self.severity_component:.3f} + "
            f"{self.weights.type_bonus:.2f}*type {self.type_component:.3f} + "
            f"{self.weights.freshness:.2f}*fresh {self.freshness_component:.3f}"
        )


def _first_signal_condition(rule: RuleDefinition) -> SignalCondition | None:
    """从规则的条件树里摘出第一个信号条件（复合规则里也能找到）。"""
    cond = rule.condition()
    if isinstance(cond, SignalCondition):
        return cond
    if isinstance(cond, ConditionGroup):
        for node in cond.walk():
            if isinstance(node, SignalCondition):
                return node
    return None


def score_hit(
    rule: RuleDefinition,
    *,
    existing_hit_count: int = 0,
    observed_signal_value: float | None = None,
    collected_at: datetime | None = None,
    now: datetime | None = None,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> ScoreBreakdown:
    """给一条命中打分，输出 0-100。

    ⚠️ 原文未明确，本项目设计（整个公式）。加权求和后 ×100::

        value_score = 100 * ( w_prio * priority.prior_score
                            + w_rare * rarity_prior(target, existing_hits)
                            + w_sev  * signal_severity(cond, observed)
                            + w_type * type_bonus(rule_type)
                            + w_fresh* freshness(collected_at) )

    Args:
        rule: 命中的规则。
        existing_hit_count: 该场景库内已有命中量，用于算稀缺度。
        observed_signal_value: 实测信号值（如实测减速度 -6.2 m/s²），信号类规则才有。
        collected_at: clip 采集时间。
        now: 参考时刻。
        weights: 权重，默认 DEFAULT_WEIGHTS。

    Returns:
        :class:`ScoreBreakdown`，含总分、分档与五个分项。
    """
    prio = rule.rule_priority.prior_score
    rare = rarity_prior(rule.target_clip_count, existing_hit_count)
    sev = signal_severity(_first_signal_condition(rule), observed_signal_value)
    tbonus = type_bonus(rule.rule_type)
    fresh = freshness(collected_at, now=now)

    total = 100.0 * (
        weights.priority * prio
        + weights.rarity * rare
        + weights.severity * sev
        + weights.type_bonus * tbonus
        + weights.freshness * fresh
    )
    total = max(0.0, min(100.0, total))
    return ScoreBreakdown(
        value_score=total,
        tier=classify_tier(total),
        priority_component=prio,
        rarity_component=rare,
        severity_component=sev,
        type_component=tbonus,
        freshness_component=fresh,
        weights=weights,
    )


def classify_tier(value_score: float) -> ValueTier:
    """按 TIER_THRESHOLDS 分档。⚠️ 阈值为本项目设计。"""
    for floor, tier in TIER_THRESHOLDS:
        if value_score >= floor:
            return tier
    return ValueTier.C


def resolve_vectorize_policy(rule: RuleDefinition) -> VectorizePolicy:
    """决定命中数据进哪一档向量化队列。

    **只看 rule_priority，刻意不看 value_score。** 原文（[S3-04] 一）把话说死了：
    「高优先级规则命中的数据优先进入向量化队列」——驱动下游分级的是规则优先级，
    不是命中本身的得分。评分的职责边界到「队内排序」为止，见 :func:`rank_hits`。
    """
    return rule.rule_priority.vectorize_policy


def rank_hits(
    scored: Iterable[tuple[RuleDefinition, ScoreBreakdown, Any]],
) -> list[tuple[RuleDefinition, ScoreBreakdown, Any]]:
    """对命中排序：先按向量化队列分级，再按优先级档位，最后按得分降序。

    排序键的三层结构正是职责边界的体现——
    队列（原文）> 优先级档位（原文）> 得分（本项目设计的队内排序）。

    Args:
        scored: ``(规则, 评分明细, 载荷)`` 三元组序列，载荷可以是任意命中行对象。

    Returns:
        排好序的列表，最该先送去 VLM 细筛的排在最前。
    """

    def key(item: tuple[RuleDefinition, ScoreBreakdown, Any]) -> tuple[int, int, float, str]:
        rule, breakdown, _ = item
        queue_rank = 0 if resolve_vectorize_policy(rule) is VectorizePolicy.PRIORITY else 1
        return (queue_rank, rule.rule_priority.rank, -breakdown.value_score, rule.rule_id)

    return sorted(scored, key=key)


# --------------------------------------------------------------------------- SQL 侧


def sql_score_expression(
    rule: RuleDefinition,
    *,
    alias: str = "clip",
    existing_hit_count: int = 0,
    time_column: str = "collect_start_time",
    signal_value_column: str | None = None,
    now: datetime | None = None,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> str:
    """把同一套评分公式渲染成 SQL 表达式，供 ``INSERT INTO ... SELECT`` 直接算分。

    Python 侧（:func:`score_hit`）与 SQL 侧必须给出一致的结果，否则批路与流路
    算出来的 value_score 会对不上。因此两边共用同一组权重常量与同一组分项定义：

    * ``priority`` / ``rarity`` / ``type_bonus`` 三项在编译期就是常数，直接内联；
    * ``severity`` 只有信号类规则有列可依，非信号类内联 0；
    * ``freshness`` 用 SQL 的日期差重算指数衰减。

    Args:
        rule: 待编译的规则。
        alias: 基表别名。
        existing_hit_count: 编译时已知的库内命中量（稀缺度分母来源）。
        time_column: 用于算新鲜度的时间列。
        signal_value_column: 覆盖信号实测值所在列。MATCH_RECOGNIZE 的输出把实测值
            聚合成了 ``peak_value``，列名与原始信号流不同，编译器在那里会传这个参数。
        now: 参考时刻，默认当前。
        weights: 权重。

    Returns:
        一个返回 DOUBLE 的 SQL 表达式字符串。
    """
    ref = now or datetime.now()
    prio = rule.rule_priority.prior_score
    rare = rarity_prior(rule.target_clip_count, existing_hit_count)
    tbonus = type_bonus(rule.rule_type)

    cond = _first_signal_condition(rule)
    if cond is not None:
        value_col = signal_value_column or cond.value_column
        val = f"{ident(alias)}.{ident(value_col)}" if alias else ident(value_col)
        thr = literal(float(cond.threshold))
        sev_expr = f"LEAST(1.0, GREATEST(0.0, ABS(CAST({val} AS DOUBLE) - {thr}) / ABS({thr})))"
    else:
        sev_expr = "0.0"

    tcol = f"{ident(alias)}.{ident(time_column)}" if alias else ident(time_column)
    fresh_expr = (
        f"POWER(0.5, GREATEST(0.0, CAST(DATEDIFF({literal(ref)}, {tcol}) AS DOUBLE))"
        f" / {literal(FRESHNESS_HALF_LIFE_DAYS)})"
    )

    return (
        "LEAST(100.0, GREATEST(0.0, 100.0 * ("
        f"{literal(weights.priority)} * {literal(prio)}"
        f" + {literal(weights.rarity)} * {literal(rare)}"
        f" + {literal(weights.severity)} * {sev_expr}"
        f" + {literal(weights.type_bonus)} * {literal(tbonus)}"
        f" + {literal(weights.freshness)} * {fresh_expr}"
        ")))"
    )


def sql_tier_expression(score_expr: str) -> str:
    """把分数表达式包成分档 CASE WHEN。阈值与 TIER_THRESHOLDS 保持同源。"""
    branches = "".join(
        f"\n    WHEN {score_expr} >= {literal(floor)} THEN {literal(tier.value)}"
        for floor, tier in TIER_THRESHOLDS
        if floor > 0.0
    )
    return f"CASE{branches}\n    ELSE {literal(ValueTier.C.value)}\n  END"


def event_window_seconds() -> int:
    """事件命中占用的窗口秒数 = 前 15 + 后 5 = 20（[S3-04] 二、三）。

    评分本身不用它，但补抽帧的成本估算要用：事件类规则的每一次命中，
    都意味着下游要对 20 秒窗口做一次加密采样。
    """
    return EVENT_WINDOW_TOTAL_SEC


def top_n(
    scored: Sequence[tuple[RuleDefinition, ScoreBreakdown, Any]], n: int
) -> list[tuple[RuleDefinition, ScoreBreakdown, Any]]:
    """取排序后的前 N 条，作为下游 VLM 细筛的候选。

    原文（[S3-04] 四）：「VLM 推理再对候选里信息密度最高的帧做语义确认」——
    「信息密度最高」由本模块的排序来兑现。N 由调用方按 GPU 预算给，原文未给具体值。
    """
    if n < 0:
        raise ValueError("n 不能为负")
    return rank_hits(scored)[:n]
