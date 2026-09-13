"""成本模型与口径核对：治理到底省了多少。

来源：a14.md 第四章（240GB 雨夜城区采集数据的一年成本账）、第六章（成本看板与
月末复盘场景、两条预算告警线）。

本模块做四件事：

1. ``CostModel``：容量 × 单价 → 成本，支持按天/按月折算与按介质拆分；
2. ``replay_case_study()``：把原文案例的七次状态流转逐行重放，核对
   ¥3,226 / ¥203 / 降幅约 94% 三个数字能不能算出来；
3. ``MonthlyReview``：月末复盘口径（1.8PB、¥58 万、分层占比、210TB、¥9.6 万、
   1.8% vs 35%）与两条告警线（成本环比 > 10%、NAS 使用率持续 > 80%）；
4. ``reconcile_source_figures()``：把原文所有能互相验算的数字对一遍，
   明确告诉使用者哪些自洽、哪些不同源不能混用。

⚠️ **一个必须说清的口径问题**：原文第四章「无生命周期治理」的基线 ¥3,226 =
NAS 常年驻留 ¥2,880 + OSS 标准常年驻留 ¥346，也就是说基线假设同一份数据在
**NAS 与 OSS 同时各留一份、且全年不动**。降幅 94% 是相对这个「双份常驻」基线算的，
不是相对「只放 OSS 标准存储」算的。若基线只算 OSS 标准常驻（¥346），
实际降幅只有 41.25%（见 ``replay_case_study()`` 的 ``reduction_vs_oss_only``）。
两个数都如实给出，不做取舍。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .records import CostDailyRow, LifecycleRecord
from .tiers import (
    DAYS_PER_MONTH,
    GB_PER_TB,
    SAMPLE_PRICE_YUAN_PER_GB_MONTH,
    TB_PER_PB,
    LifecycleStage,
    StorageMedia,
)

__all__ = [
    "CostModel",
    "CaseSnapshot",
    "CASE_DATA_ID",
    "CASE_SIZE_GB",
    "CASE_TIMELINE",
    "CASE_COST_BOOK",
    "SOURCE_REDUCTION_RATE",
    "replay_case_study",
    "MonthlyReview",
    "SOURCE_MONTHLY_REVIEW",
    "ALERT_COST_MOM_GROWTH",
    "ALERT_NAS_USAGE_RATIO",
    "COST_VS_DATA_GROWTH_RATIO",
    "budget_alerts",
    "aggregate_cost_daily",
    "dashboard_metrics",
    "reconcile_source_figures",
]


# --------------------------------------------------------------------------- 成本模型


@dataclass(frozen=True, slots=True)
class CostModel:
    """存储成本模型：``成本 = 容量 × 介质单价``（原文第六章「容量 × 介质单价折算」）。

    :param price_yuan_per_gb_month: 单价表（元/GB·月）。默认用原文第四章案例的示例单价，
        生产环境必须用云厂商实际计价覆盖——原文第二章亦注明「示意值，以云厂商实际计价为准」。
    :param days_per_month: 月费折算到天的天数，默认 30（见 tiers.DAYS_PER_MONTH 的 ⚠️）。
    """

    price_yuan_per_gb_month: Mapping[StorageMedia, float] = field(
        default_factory=lambda: dict(SAMPLE_PRICE_YUAN_PER_GB_MONTH)
    )
    days_per_month: int = DAYS_PER_MONTH

    def unit_price(self, media: StorageMedia) -> float:
        """取某介质单价（元/GB·月）。

        :raises KeyError: 单价表里没有这个介质——宁可炸，也不能按 0 元静默低估成本。
        """
        try:
            return self.price_yuan_per_gb_month[media]
        except KeyError as exc:
            raise KeyError(
                f"单价表缺少介质 {media.value}；现有 "
                f"{[m.value for m in self.price_yuan_per_gb_month]}"
            ) from exc

    def monthly_cost(self, media: StorageMedia, size_gb: float) -> float:
        """月成本（元）。"""
        if size_gb < 0:
            raise ValueError(f"容量不能为负: {size_gb}")
        return size_gb * self.unit_price(media)

    def daily_cost(self, media: StorageMedia, size_gb: float) -> float:
        """当日折算成本（元）——成本日表 ``daily_cost_yuan`` 的口径。"""
        return self.monthly_cost(media, size_gb) / self.days_per_month

    def cost_for_days(self, media: StorageMedia, size_gb: float, days: float) -> float:
        """在某介质上停留 N 天的成本（元）。案例里的「占用 9 天 ¥72」就是这么算的。"""
        if days < 0:
            raise ValueError(f"天数不能为负: {days}")
        return self.daily_cost(media, size_gb) * days

    def cost_for_months(self, media: StorageMedia, size_gb: float, months: float) -> float:
        """在某介质上停留 N 个月的成本（元）。"""
        if months < 0:
            raise ValueError(f"月数不能为负: {months}")
        return self.monthly_cost(media, size_gb) * months

    def monthly_cost_tb(self, media: StorageMedia, size_tb: float) -> float:
        """按 TB 算月成本（元）——成本日表按 TB 聚合容量。"""
        return self.monthly_cost(media, size_tb * GB_PER_TB)

    def blended_price(self, mix: Mapping[StorageMedia, float]) -> float:
        """按分层占比算混合单价（元/GB·月）。

        :param mix: 各介质容量占比，应加总为 1（允许 ±1e-6 误差）。
        :raises ValueError: 占比加总明显不为 1——分层占比算错会让整张看板失真。
        """
        total = sum(mix.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"分层占比须加总为 1，当前为 {total}")
        return sum(self.unit_price(m) * w for m, w in mix.items())


# --------------------------------------------------------------------------- 案例重放

#: 案例数据 ID（原文第四章：「一份 240GB 的雨夜城区采集数据
#: （COLLECT_BP_20260301123045_b7e2）」）。
CASE_DATA_ID: str = "COLLECT_BP_20260301123045_b7e2"

#: 案例数据体积（GB）。
CASE_SIZE_GB: float = 240.0


@dataclass(frozen=True, slots=True)
class CaseSnapshot:
    """案例时间线上的一个快照点（原文第四章表格的一行，字段全为原文原话）。"""

    at: str
    media: StorageMedia
    stage: LifecycleStage
    trigger: str


#: 一年七次状态流转（原文第四章表格逐行落地）。
CASE_TIMELINE: tuple[CaseSnapshot, ...] = (
    CaseSnapshot(
        "03-01",
        StorageMedia.OSS_STANDARD,
        LifecycleStage.WARM,
        "采集上传落湖，文件元信息实时写入湖仓",
    ),
    CaseSnapshot(
        "03-10",
        StorageMedia.OSS_STANDARD,
        LifecycleStage.WARM,
        "入数据集 ds_rainy_night_v3，血缘引用 +1，触发「被引用提档保留」",
    ),
    CaseSnapshot(
        "03-11", StorageMedia.NAS, LifecycleStage.HOT, "训练任务创建，预热至 NAS（预热任务登记）"
    ),
    CaseSnapshot(
        "03-20",
        StorageMedia.OSS_STANDARD,
        LifecycleStage.WARM,
        "7 天缓冲期满且 checksum 校验通过，NAS 副本淘汰（淘汰 ≠ 删除）",
    ),
    CaseSnapshot(
        "04-30",
        StorageMedia.OSS_IA,
        LifecycleStage.COLD,
        "30 天无访问且提档保留期满，自动降冷（成本降至 0.5x）",
    ),
    CaseSnapshot(
        "07-29",
        StorageMedia.OSS_ARCHIVE,
        LifecycleStage.ARCHIVE,
        "连续 90 天无访问，归档流转（成本降至 0.15x）",
    ),
    CaseSnapshot(
        "+365 天",
        StorageMedia.OSS_ARCHIVE,
        LifecycleStage.ARCHIVE,
        "保留期满但血缘引用 > 0 → 删除三重确认拦截，继续留存",
    ),
)

#: 原文第四章成本账表格（费用项 → (无治理 元, 有治理 元, 原文口径说明)）。
#: 数字逐字照抄，用于与 replay_case_study() 的计算结果对账。
CASE_COST_BOOK: dict[str, tuple[float, float, str]] = {
    "NAS 副本": (
        2880.0,
        72.0,
        "无治理：常年驻留（240GB×12 月）¥2,880；有治理：占用 9 天（预热 → 淘汰）¥72",
    ),
    "OSS 标准存储": (346.0, 58.0, "无治理：常年驻留 ¥346；有治理：2 个月 ¥58"),
    "OSS 低频 / 归档": (0.0, 73.0, "无治理：—；有治理：3 个月 ¥43 + 7 个月 ¥30"),
    "单份数据年成本": (3226.0, 203.0, "¥3,226 vs ¥203（降幅约 94%）"),
}

#: 原文第四章结论：降幅约 94%。
SOURCE_REDUCTION_RATE: float = 0.94

#: 案例各段停留时长（⚠️ 原文只给日期与「2 个月 / 3 个月 / 7 个月 / 9 天」的表述，
#: 本项目按原文成本表的表述逐字落地，不自行按日历重算天数）。
_CASE_SEGMENTS: tuple[tuple[StorageMedia, float, str], ...] = (
    (StorageMedia.NAS, 9 / DAYS_PER_MONTH, "占用 9 天（03-11 预热 → 03-20 淘汰）"),
    (StorageMedia.OSS_STANDARD, 2.0, "2 个月（03-01 → 04-30）"),
    (StorageMedia.OSS_IA, 3.0, "3 个月（04-30 → 07-29）"),
    (StorageMedia.OSS_ARCHIVE, 7.0, "7 个月（07-29 → 次年 02-28）"),
)


def replay_case_study(model: CostModel | None = None) -> dict[str, object]:
    """重放原文案例，核对 ¥3,226 / ¥203 / 94% 三个数字。

    计算口径（全部来自原文第四章）::

        无治理基线 = NAS 240GB × 12 月 × 1.0    = ¥2,880.00
                   + OSS标准 240GB × 12 月 × 0.12 = ¥345.60
                   合计                           = ¥3,225.60  → 原文写 ¥3,226

        有治理     = NAS      240GB × 9/30 月 × 1.0   = ¥72.00
                   + OSS标准  240GB × 2 月   × 0.12  = ¥57.60   → 原文写 ¥58
                   + OSS低频  240GB × 3 月   × 0.06  = ¥43.20   → 原文写 ¥43
                   + OSS归档  240GB × 7 月   × 0.018 = ¥30.24   → 原文写 ¥30
                   合计                              = ¥203.04  → 原文写 ¥203

        降幅 = 1 − 203.04 / 3225.60 = 93.71%  → 原文写「降幅约 94%」

    结论：原文的 94% **口径成立**，算出来是 93.71%，原文做了向上取整到整数百分点。
    但基线含「NAS 与 OSS 标准双份常年驻留」这一层假设（见模块 docstring 的 ⚠️），
    因此本函数同时给出只对 OSS 标准基线的降幅 ``reduction_vs_oss_only``。

    :param model: 成本模型；默认用原文示例单价。
    :returns: 明细、合计、两种口径的降幅，以及与原文数字的逐项对账。
    """
    m = model or CostModel()
    gb = CASE_SIZE_GB

    baseline_nas = m.cost_for_months(StorageMedia.NAS, gb, 12)
    baseline_std = m.cost_for_months(StorageMedia.OSS_STANDARD, gb, 12)
    baseline_total = baseline_nas + baseline_std

    governed: list[dict[str, object]] = []
    for media, months, note in _CASE_SEGMENTS:
        cost = m.cost_for_months(media, gb, months)
        governed.append(
            {
                "media": media.value,
                "months": round(months, 4),
                "unit_price": m.unit_price(media),
                "cost_yuan": round(cost, 2),
                "note": note,
            }
        )
    governed_total = sum(float(g["cost_yuan"]) for g in governed)

    reduction = 1 - governed_total / baseline_total
    reduction_vs_oss_only = 1 - governed_total / baseline_std

    return {
        "data_id": CASE_DATA_ID,
        "size_gb": gb,
        "timeline": [
            {"at": s.at, "media": s.media.value, "stage": s.stage.value, "trigger": s.trigger}
            for s in CASE_TIMELINE
        ],
        "baseline": {
            "nas_yuan": round(baseline_nas, 2),  # 2880.0，原文 ¥2,880 ✅
            "oss_standard_yuan": round(baseline_std, 2),  # 345.6，原文 ¥346 ✅（四舍五入）
            "total_yuan": round(baseline_total, 2),  # 3225.6，原文 ¥3,226 ✅
            "caliber": "NAS 常年驻留 + OSS 标准常年驻留，双份 12 个月",
        },
        "governed": {
            "segments": governed,
            "total_yuan": round(governed_total, 2),  # 203.04，原文 ¥203 ✅
        },
        "reduction_rate": round(reduction, 4),  # 0.9371
        "reduction_rate_pct": f"{reduction:.2%}",  # 93.71%
        "source_claim": SOURCE_REDUCTION_RATE,  # 原文 0.94
        "claim_verified": abs(reduction - SOURCE_REDUCTION_RATE) < 0.01,
        "reduction_vs_oss_only": round(reduction_vs_oss_only, 4),
        "caliber_note": (
            f"原文「降幅约 94%」口径成立：精确值 {reduction:.2%}，原文取整到 94%。"
            "但该降幅的基线是「NAS + OSS 标准双份常年驻留」；若基线只算 OSS 标准常驻，"
            f"降幅为 {reduction_vs_oss_only:.1%}。引用 94% 时必须连基线口径一起说。"
        ),
    }


# --------------------------------------------------------------------------- 月末复盘


@dataclass(frozen=True, slots=True)
class MonthlyReview:
    """月末复盘口径（原文第六章「月末复盘场景」的全部数字）。

    各字段都能独立核算，``self_check()`` 会把能互相验算的关系跑一遍。
    """

    total_capacity_pb: float
    monthly_cost_yuan: float
    tier_mix: Mapping[StorageMedia, float]
    released_tb: float
    saved_yuan: float
    ingested_tb: float
    cost_mom_growth: float
    data_growth: float
    preheat_hit_rate: float
    archive_restore_per_day: int

    @property
    def total_capacity_tb(self) -> float:
        """总容量（TB）。PB → TB 按 1024 换算（见 tiers 的 ⚠️）。"""
        return self.total_capacity_pb * TB_PER_PB

    @property
    def net_capacity_growth_tb(self) -> float:
        """净增容量（TB）= 新增入湖 − 治理释放。原文：210TB 释放与 190TB 新增「对冲」。"""
        return self.ingested_tb - self.released_tb

    @property
    def implied_saving_yuan_per_gb_month(self) -> float:
        """⚠️ 本项目推算：治理释放的每 GB 月均节省 = 节省额 / 释放容量。

        原文给了「释放 210TB、节省 ¥9.6 万」但没说动作构成（NAS 淘汰 / 降冷 / 删除各占多少），
        这个反推值可用来判断动作构成是否合理：
        NAS→OSS 标准单价差 0.88 元/GB·月，OSS 标准→低频差 0.06 元/GB·月，
        实际值落在两者之间说明是混合动作，落在区间外说明数据有问题。
        """
        return self.saved_yuan / (self.released_tb * GB_PER_TB)

    @property
    def cost_growth_over_data_growth(self) -> float:
        """成本增速 / 数据增速。原文引言给的目标是「约 1/20」。"""
        return self.cost_mom_growth / self.data_growth

    def self_check(self) -> dict[str, object]:
        """复盘数字的自洽性核对。

        :returns: 分层占比是否加总为 1、成本/数据增速比是否接近 1/20、
            隐含节省单价是否落在合理区间。
        """
        mix_total = sum(self.tier_mix.values())
        ratio = self.cost_growth_over_data_growth
        implied = self.implied_saving_yuan_per_gb_month
        nas_to_std = (
            SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.NAS]
            - SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.OSS_STANDARD]
        )
        std_to_ia = (
            SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.OSS_STANDARD]
            - SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.OSS_IA]
        )
        return {
            "tier_mix_sums_to_one": abs(mix_total - 1.0) < 1e-9,
            "tier_mix_total": round(mix_total, 6),
            "cost_growth_over_data_growth": round(ratio, 6),
            "as_one_over_n": round(1 / ratio, 2) if ratio else None,
            "matches_one_twentieth": abs(ratio - COST_VS_DATA_GROWTH_RATIO) < 0.01,
            "implied_saving_yuan_per_gb_month": round(implied, 4),
            "implied_saving_in_range": std_to_ia <= implied <= nas_to_std,
            "implied_saving_range": (std_to_ia, nas_to_std),
        }


#: 原文第六章月末复盘场景的原始数字，一个不改。
SOURCE_MONTHLY_REVIEW = MonthlyReview(
    total_capacity_pb=1.8,  # 「全湖存储 1.8PB」
    monthly_cost_yuan=580_000.0,  # 「月成本 ¥58 万」
    tier_mix={  # 「分层占比 热 6% / 标准 24% / 低频 38% / 归档 32%」
        StorageMedia.NAS: 0.06,
        StorageMedia.OSS_STANDARD: 0.24,
        StorageMedia.OSS_IA: 0.38,
        StorageMedia.OSS_ARCHIVE: 0.32,
    },
    released_tb=210.0,  # 「当月治理动作释放 210TB」
    saved_yuan=96_000.0,  # 「节省成本 ¥9.6 万」
    ingested_tb=190.0,  # 「与新增入湖 190TB 对冲」
    cost_mom_growth=0.018,  # 「总成本环比仅增长 1.8%」
    data_growth=0.35,  # 「远低于数据量 35% 的增速」
    preheat_hit_rate=0.91,  # 「同期预热命中率 91%」
    archive_restore_per_day=23,  # 「归档取回 23 次/日」
)

#: 预算告警线一：存储成本环比增长 > 10%（原文第六章）。
ALERT_COST_MOM_GROWTH: float = 0.10

#: 预算告警线二：NAS 使用率持续 > 80%（原文第六章；与第三章水位淘汰线是同一个数）。
ALERT_NAS_USAGE_RATIO: float = 0.80

#: 原文引言的治理目标：「让存储成本增速只有数据增速的约 1/20」。
COST_VS_DATA_GROWTH_RATIO: float = 1 / 20


def budget_alerts(
    *, cost_mom_growth: float, nas_usage_ratio: float, sustained: bool = True
) -> list[dict[str, object]]:
    """两条预算告警线的判定（原文第六章）。

    :param cost_mom_growth: 存储成本环比增长率（0.12 表示 +12%）。
    :param nas_usage_ratio: NAS 当前使用率（0.85 表示 85%）。
    :param sustained: NAS 使用率是否「持续」超线——原文措辞是「持续 > 80%」，
        瞬时尖峰不告警。⚠️ 原文未定义「持续」的观测窗口，由调用方判定后传入。
    :returns: 命中的告警列表；空列表表示两条线都在健康区间。
    """
    alerts: list[dict[str, object]] = []
    if cost_mom_growth > ALERT_COST_MOM_GROWTH:
        alerts.append(
            {
                "code": "cost_mom_growth",
                "level": "warning",
                "message": (
                    f"存储成本环比增长 {cost_mom_growth:.1%} > "
                    f"{ALERT_COST_MOM_GROWTH:.0%} 告警线，驱动规则调优与容量规划"
                ),
                "value": cost_mom_growth,
                "threshold": ALERT_COST_MOM_GROWTH,
            }
        )
    if nas_usage_ratio > ALERT_NAS_USAGE_RATIO and sustained:
        alerts.append(
            {
                "code": "nas_usage",
                "level": "warning",
                "message": (
                    f"NAS 使用率持续 {nas_usage_ratio:.0%} > "
                    f"{ALERT_NAS_USAGE_RATIO:.0%}，触发水位淘汰与容量规划"
                ),
                "value": nas_usage_ratio,
                "threshold": ALERT_NAS_USAGE_RATIO,
            }
        )
    return alerts


# --------------------------------------------------------------------------- 聚合


def aggregate_cost_daily(
    records: Iterable[LifecycleRecord],
    *,
    stat_date: date,
    model: CostModel | None = None,
    action_volumes: Mapping[tuple[str, ...], Mapping[str, float]] | None = None,
    nas_peak_usage: float = 0.0,
    preheat_hit_rate: float = 0.0,
    archive_restore_count: int = 0,
    baseline_media: StorageMedia = StorageMedia.OSS_STANDARD,
    previous_day: Iterable[CostDailyRow] | None = None,
) -> list[CostDailyRow]:
    """把明细快照聚合成 ``dws_closed_loop_storage_cost_daily`` 的行。

    聚合维度 = 「日期 × 介质 × 分层 × 数据类型 × 来源域」（原文第四章②）。

    除容量与成本外，本函数还负责算出第六章看板要的三项派生指标——
    它们在注册表里各有一列，下游 ``ads_storage_cost_dashboard`` 直接读：

    ==================  ====================================================
    ``baseline_cost_yuan``  无治理基线成本 = 同样的容量全放 ``baseline_media``
    ``saved_cost_yuan``     治理释放成本 = 基线成本 − 实际成本（原文第六章原话）
    ``cost_mom_rate``       成本环比 =（今日 − 昨日）/ 昨日，> 10% 触发预算告警
    ==================  ====================================================

    :param records: 当日的生命周期状态快照。
    :param stat_date: 统计日期（``datetime.date``）。
    :param model: 成本模型，默认原文示例单价。
    :param action_volumes: 按五维主键给出的当日治理动作量，形如
        ``{(media, stage, data_type, source_domain): {"preheat": 1.2, ...}}``，
        键名取 ``preheat`` / ``evict`` / ``tier_down`` / ``delete``。
        一般由 ``scheduler`` 从当轮决策里汇总后传入。
    :param nas_peak_usage: 当日 NAS 峰值使用率，写入每一行（看板口径为全局指标）。
    :param preheat_hit_rate: 当日预热命中率 = 训练预热命中 / 总预热请求（原文第六章）。
    :param archive_restore_count: 当日归档取回次数。
    :param baseline_media: 「无治理基线」假定数据都放哪一档。
        ⚠️ 原文未明确整湖基线介质，本项目取 OSS 标准存储——第四章案例那个
        「NAS + OSS 标准双份常驻」的基线只适用于上过 NAS 的训练数据，不能整湖套用。
    :param previous_day: 昨日的成本日表行，用于算 ``cost_mom_rate``；
        不传则环比留 0（第一天没有环比可算，写 0 比写假值诚实）。
    :returns: 成本日表行列表，按五维主键排序。
    """
    m = model or CostModel()
    buckets: dict[tuple[str, ...], CostDailyRow] = {}
    prev_cost: dict[tuple[Any, ...], float] = {
        row.pk[1:]: row.daily_cost_yuan for row in (previous_day or ())
    }

    for rec in records:
        if rec.lifecycle_stage is LifecycleStage.DELETED:
            continue  # 已删除的不再占容量，也不再计费
        key = (
            rec.storage_media.value,
            rec.lifecycle_stage.value,
            rec.data_type.code if rec.data_type else "",
            rec.source_domain,
        )
        row = buckets.get(key)
        if row is None:
            row = CostDailyRow(
                stat_date=stat_date,
                storage_media=rec.storage_media,
                lifecycle_stage=rec.lifecycle_stage,
                data_type=key[2],
                source_domain=key[3],
                nas_peak_usage=nas_peak_usage,
                preheat_hit_rate=preheat_hit_rate,
                archive_restore_count=archive_restore_count,
            )
            buckets[key] = row
        row.file_count += 1
        row.total_capacity_tb += rec.size_tb
        row.daily_cost_yuan += m.daily_cost(rec.storage_media, rec.size_gb)
        row.baseline_cost_yuan += m.daily_cost(baseline_media, rec.size_gb)

    for key, volumes in (action_volumes or {}).items():
        key = tuple(key)
        row = buckets.get(key)
        if row is None:
            # 动作量按「动作发生前」的五维归集，而动作往往正好把数据搬离这个格子
            # （降冷后 media 变了、删除后数据没了），所以这里必须补一条零容量行，
            # 否则当日治理动作量会被静默吞掉——删除动作更是一条都留不下来。
            row = CostDailyRow(
                stat_date=stat_date,
                storage_media=StorageMedia(key[0]),
                lifecycle_stage=LifecycleStage(key[1]),
                data_type=key[2],
                source_domain=key[3],
                nas_peak_usage=nas_peak_usage,
                preheat_hit_rate=preheat_hit_rate,
                archive_restore_count=archive_restore_count,
            )
            buckets[key] = row
        row.preheat_volume_tb += volumes.get("preheat", 0.0)
        row.evict_volume_tb += volumes.get("evict", 0.0)
        row.tier_down_volume_tb += volumes.get("tier_down", 0.0)
        row.delete_volume_tb += volumes.get("delete", 0.0)

    for row in buckets.values():
        # 成本节省额 = 无治理基线成本 − 实际成本（原文第六章看板口径原话）
        row.saved_cost_yuan = row.baseline_cost_yuan - row.daily_cost_yuan
        yesterday = prev_cost.get(row.pk[1:])
        if yesterday:
            row.cost_mom_rate = (row.daily_cost_yuan - yesterday) / yesterday

    return sorted(buckets.values(), key=lambda r: r.pk)


def dashboard_metrics(
    rows: Iterable[CostDailyRow],
    *,
    model: CostModel | None = None,
    baseline_media: StorageMedia = StorageMedia.OSS_STANDARD,
) -> dict[str, object]:
    """``ads_storage_cost_dashboard`` 的六项指标口径（原文第六章表格）。

    ==============================  ==========================================
    指标                             计算口径（原文原话）
    ==============================  ==========================================
    总存储容量 / 月存储成本           各介质容量加总；容量 × 介质单价折算
    分层占比                         NAS 热 / OSS 标准 / 低频 / 归档四档容量分布
    治理动作量                       日预热 / 降冷 / 淘汰 / 删除数据量
    成本节省额                       治理释放成本 = 无治理基线成本 − 实际成本
    NAS 峰值使用率 / 预热命中率       容量水位监控；训练预热命中 / 总预热请求
    归档取回次数                     冷数据取回频次
    ==============================  ==========================================

    :param rows: 某一天（或某一段）的成本日表行。
    :param model: 成本模型。
    :param baseline_media: 「无治理基线」假定全部数据都放哪一档。
        ⚠️ 原文未明确基线介质，本项目取 OSS 标准存储——第四章案例的基线含 NAS 双份常驻，
        那个口径只适用于曾经上过 NAS 的训练数据，不能整湖套用。
    :returns: 看板六项指标。
    """
    m = model or CostModel()
    rows = list(rows)
    total_tb = sum(r.total_capacity_tb for r in rows)
    daily_cost = sum(r.daily_cost_yuan for r in rows)

    mix: dict[str, float] = {}
    for r in rows:
        mix[r.storage_media.value] = mix.get(r.storage_media.value, 0.0) + r.total_capacity_tb
    tier_mix = {k: (v / total_tb if total_tb else 0.0) for k, v in mix.items()}

    # 基线优先用行里已经算好的（aggregate_cost_daily 逐条按介质算，比整湖一刀切准），
    # 行里没有（老数据 / 手工构造）才退回「全量按 baseline_media 计价」。
    stored_baseline = sum(r.baseline_cost_yuan for r in rows)
    baseline_daily = stored_baseline or m.daily_cost(baseline_media, total_tb * GB_PER_TB)

    return {
        "total_capacity_tb": round(total_tb, 4),
        "total_capacity_pb": round(total_tb / TB_PER_PB, 6),
        "daily_cost_yuan": round(daily_cost, 2),
        "monthly_cost_yuan": round(daily_cost * m.days_per_month, 2),
        "tier_mix": {k: round(v, 4) for k, v in sorted(tier_mix.items())},
        "governance_volume_tb": {
            "preheat": round(sum(r.preheat_volume_tb for r in rows), 6),
            "tier_down": round(sum(r.tier_down_volume_tb for r in rows), 6),
            "evict": round(sum(r.evict_volume_tb for r in rows), 6),
            "delete": round(sum(r.delete_volume_tb for r in rows), 6),
        },
        "file_count": sum(r.file_count for r in rows),
        "baseline_cost_yuan": round(baseline_daily, 2),
        "cost_saved_yuan": round(baseline_daily - daily_cost, 2),
        "cost_saved_caliber": (
            "无治理基线取自成本日表的 baseline_cost_yuan"
            if stored_baseline
            else f"无治理基线按全量 {baseline_media.value} 计价"
        ),
        "cost_mom_rate": round(max((r.cost_mom_rate for r in rows), key=abs, default=0.0), 6),
        "nas_peak_usage": round(max((r.nas_peak_usage for r in rows), default=0.0), 4),
        "preheat_hit_rate": round(max((r.preheat_hit_rate for r in rows), default=0.0), 4),
        "archive_restore_count": max((r.archive_restore_count for r in rows), default=0),
    }


# --------------------------------------------------------------------------- 口径核对


def reconcile_source_figures(model: CostModel | None = None) -> dict[str, object]:
    """把原文所有能互相验算的数字对一遍，如实给出哪些自洽、哪些不同源。

    这是本子系统对「原文提到降本 94%，核对其口径」这一要求的正式回答。

    :returns: 四组对账结论。
    """
    m = model or CostModel()
    case = replay_case_study(m)
    review = SOURCE_MONTHLY_REVIEW
    check = review.self_check()

    # 用案例示例单价 + 月末复盘的分层占比，反推 1.8PB 应该花多少
    blended = m.blended_price(dict(review.tier_mix))
    implied_monthly = blended * review.total_capacity_tb * GB_PER_TB
    stated_monthly = review.monthly_cost_yuan

    return {
        "① 案例降幅 94%": {
            "verdict": "✅ 口径成立",
            "computed": case["reduction_rate_pct"],
            "source": "约 94%",
            "note": case["caliber_note"],
        },
        "② 成本增速 ≈ 数据增速的 1/20": {
            "verdict": "✅ 口径成立" if check["matches_one_twentieth"] else "⚠️ 与原文不符",
            "computed": f"1.8% / 35% = 1/{check['as_one_over_n']}",
            "source": "约 1/20（引言）",
            "note": "月末复盘的 1.8% 与 35% 两个数确实落在引言承诺的 1/20 量级上。",
        },
        "③ 分层占比加总": {
            "verdict": "✅ 加总为 100%" if check["tier_mix_sums_to_one"] else "⚠️ 加总不为 100%",
            "computed": check["tier_mix_total"],
            "source": "热 6% / 标准 24% / 低频 38% / 归档 32%",
        },
        "④ 1.8PB × 示例单价 vs 月成本 ¥58 万": {
            "verdict": "⚠️ 两套数字不同源，不可混用",
            "computed_monthly_yuan": round(implied_monthly, 2),
            "stated_monthly_yuan": stated_monthly,
            "blended_price_yuan_per_gb_month": round(blended, 6),
            "implied_actual_price_yuan_per_gb_month": round(
                stated_monthly / (review.total_capacity_tb * GB_PER_TB), 6
            ),
            "note": (
                f"用第四章的示例单价（NAS 1.0 / 标准 0.12 / 低频 0.06 / 归档 0.018）"
                f"配第六章的分层占比，1.8PB 只需 ¥{implied_monthly:,.0f}/月，"
                f"与原文看板的 ¥{stated_monthly:,.0f}/月差约 "
                f"{stated_monthly / implied_monthly:.1f} 倍。原因是第四章单价自称「示例单价」、"
                f"第六章看板注明「按云厂商实际计价折算」——两者本就不同源。"
                f"本项目的处理：示例单价只用于复现案例，看板一律用注入的真实单价表。"
            ),
        },
        "⑤ 释放 210TB 省 ¥9.6 万的隐含单价": {
            "verdict": (
                "✅ 落在合理区间" if check["implied_saving_in_range"] else "⚠️ 超出合理区间"
            ),
            "computed_yuan_per_gb_month": check["implied_saving_yuan_per_gb_month"],
            "range": check["implied_saving_range"],
            "note": (
                "原文未给出 210TB 的动作构成。反推出的 "
                f"{check['implied_saving_yuan_per_gb_month']} 元/GB·月 落在"
                "「标准→低频 0.06」与「NAS→标准 0.88」之间，说明当月是 NAS 淘汰与降冷的混合动作，"
                "数字自洽。"
            ),
        },
    }
