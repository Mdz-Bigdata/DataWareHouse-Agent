"""深度对账：ADS 数据产品矩阵——11 张表、OTA 灰度三条件、地理网格热力图、难例采纳率。

对账基准是两篇原文：

  [S1-05]  《11 张 ADS 数据闭环表开箱即用：智驾数据产品矩阵全览》
           公众号「小周谈智驾数据闭环」2026-08-28（本仓库快照 a15.md）
  [S1-全景]《智驾数据闭环的湖仓架构全景：8 环节闭环 × 11 数据域 × 79+ 张表 × 6 大场景》
           同公众号 2026-09-08（本仓库快照 a5.md）

本文件的断言纪律与 ``tests/deep/test_quality.py`` 一致：**打在原文给的具体数字上**，
不满足于「函数能跑通」。原文原话逐条对应到下面的测试节：

    [S1-05] 二    11 张表 × 六大主题 × 六大业务平台，序号与服务对象逐字
    [S1-05] 四 3  「v3.2 评测挖出 3,200 条难例（夜间行人 900、逆光车辆 700），
                   训练平台采纳 2,800 条（采纳率 87.5%）」——难例采纳率的分子分母
    [S1-05] 五表7 「91.2% vs 87.5%，夜间 +8.3pp，高速回归 −1.2pp → 修复后再 OTA」
    [S1-05] 五表8 「升级成功率 99.2%，发布后一周回传触发量环比增长 30%，
                   安全相关问题 0 起 → 确认全量推送」——三条件的**与**关系与三个边界
    [S1-05] 五表9 「触发集中在城区晚高峰路口；某区域『AEB 误触发』周环比上升 45%」
                   ——地理网格粒度、聚合口径、热力等级分档
    [S1-全景] 七  双路查询选路：内表物化 / 外部表即席查 / 向量索引（委派）
    [S1-全景] 九  六项闭环业务服务 + 代表 API，以及「11 张表都要有服务化出口」
"""

from __future__ import annotations

import inspect
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from adas_lakehouse.ads import constants as C
from adas_lakehouse.ads import demo as D
from adas_lakehouse.ads import gateway as G
from adas_lakehouse.ads import geo
from adas_lakehouse.ads import materialize as M
from adas_lakehouse.ads import products as P
from adas_lakehouse.ads import query as Q
from adas_lakehouse.ads import routing as R
from adas_lakehouse.ads import schema as S
from adas_lakehouse.ads import services as SV
from adas_lakehouse.ads.errors import (
    AdsQueryError,
    AuthorizationError,
    ServiceUnavailableError,
    UnknownColumnError,
    UnknownTableError,
)

# ===========================================================================
# 共用装配
# ===========================================================================

#: 原文表 8 的三个观察值，整份文件反复用它们做基准。
SOURCE_SUCCESS_RATE = 0.992
SOURCE_TRIGGER_GROWTH = 0.30
SOURCE_SAFETY_ISSUES = 0


@pytest.fixture()
def suite() -> SV.ClosedLoopServiceSuite:
    """装满原文案例数字的六项服务装配体（不连 StarRocks）。"""
    return SV.ClosedLoopServiceSuite(Q.AdsQueryService(D.DemoRowSource()))


@pytest.fixture()
def empty_suite() -> SV.ClosedLoopServiceSuite:
    """一行数据都没有的装配体——用来验「缺数据不放行」而不是「缺数据当没问题」。"""
    return SV.ClosedLoopServiceSuite(Q.AdsQueryService(Q.StaticRowSource()))


def gate(**overrides) -> SV.OtaGateDecision:
    """按原文三个观察值造一个放行判定，只覆盖要测的那一项。"""
    kwargs = {
        "ota_task_id": "OTA-v3.3-GREY",
        "deploy_success_rate": SOURCE_SUCCESS_RATE,
        "post_release_trigger_growth_rate": SOURCE_TRIGGER_GROWTH,
        "safety_issue_count": SOURCE_SAFETY_ISSUES,
    }
    kwargs.update(overrides)
    return SV.evaluate_ota_release_gate(**kwargs)


# ===========================================================================
# 一、原文参数逐字对账（判据 A）
# ===========================================================================


def test_table8_three_gate_numbers_are_verbatim():
    """★三条件的三个数字★：99.2% / +30% / 0 起，一个字都不许改。

    原文原话（[S1-05] 第五章表 8）：「v3.3 灰度推送 500 台车：升级成功率 99.2%，
    发布后一周回传触发量环比增长 30%（新版本主动采集策略生效），安全相关问题 0 起
    → 确认全量推送」。
    """
    assert C.OTA_GATE_MIN_SUCCESS_RATE == 0.992
    assert C.OTA_GATE_MIN_TRIGGER_GROWTH_RATE == 0.30
    assert C.OTA_GATE_MAX_SAFETY_ISSUE_COUNT == 0
    assert C.OTA_GATE_CONDITION_COUNT == 3
    # 案例值与门槛值必须是同一个对象：同一个数不许在两处各写一遍，否则迟早漂移
    assert C.OTA_GATE_MIN_SUCCESS_RATE == C.OTA_DEMO_SUCCESS_RATE
    assert C.OTA_GATE_MIN_TRIGGER_GROWTH_RATE == C.OTA_DEMO_TRIGGER_GROWTH_RATE
    assert C.OTA_GATE_MAX_SAFETY_ISSUE_COUNT == C.OTA_DEMO_SAFETY_ISSUE_COUNT
    # 灰度推送 500 台车、发布后「一周」= 7 天
    assert C.OTA_DEMO_GREY_VEHICLE_COUNT == 500
    assert C.OTA_POST_RELEASE_OBSERVE_DAYS == 7
    assert C.OTA_RELEASE_CHANNELS == ("internal", "grey", "full")


def test_hard_case_library_numbers_are_verbatim():
    """★难例库★：3,200 挖出 / 900 夜间行人 / 700 逆光车辆 / 2,800 采纳 / 87.5%。"""
    assert C.HARD_CASE_DEMO_TOTAL_COUNT == 3_200
    assert C.HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT == 900
    assert C.HARD_CASE_DEMO_BACKLIT_VEHICLE_COUNT == 700
    assert C.HARD_CASE_DEMO_ADOPTED_COUNT == 2_800
    assert C.HARD_CASE_DEMO_ADOPTION_RATE == 0.875
    assert C.HARD_CASE_DEMO_MISS_RATE_DROP_RATIO == 0.60
    assert C.HARD_CASE_DEMO_RETRAIN_MODEL_VERSION == "v3.3"
    # 原文把 87.5% 写成结果，2,800 / 3,200 必须真的等于它——不是四舍五入凑出来的
    assert C.HARD_CASE_DEMO_ADOPTED_COUNT / C.HARD_CASE_DEMO_TOTAL_COUNT == 0.875


def test_badcase_root_cause_numbers_are_verbatim():
    """表 3：3,200 个 Badcase / 感知漏检 45% / 夜间行人 28% / 补采 2,000 / 下降 60%。"""
    assert C.BADCASE_DEMO_TOTAL_COUNT == 3_200
    assert C.BADCASE_DEMO_PERCEPTION_MISS_RATIO == 0.45
    assert C.BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO == 0.28
    assert C.BADCASE_DEMO_TARGETED_COLLECT_COUNT == 2_000
    assert C.BADCASE_DEMO_MISS_RATE_DROP_RATIO == 0.60
    assert C.BADCASE_DEMO_MODEL_VERSION == "v3.2"


def test_scene_library_numbers_are_verbatim():
    """表 4：1,200 个标签 / 覆盖度 82% / 施工区域 150 条 / 达标线 2,000 条 / 两周。"""
    assert C.SCENE_LIBRARY_TAG_COUNT == 1_200
    assert C.SCENE_LIBRARY_COVERAGE_RATE == 0.82
    assert C.SCENE_GAP_DEMO_CURRENT_COUNT == 150
    assert C.SCENE_GAP_DEMO_TARGET_COUNT == 2_000
    assert C.SCENE_GAP_DEMO_SUPPLEMENT_WEEKS == 2
    # 「状态流转 COVERED」——三态顺序即原文给的流转方向
    assert C.SCENE_COVERAGE_STATUS_FLOW == ("GAP", "FILLING", "COVERED")


def test_model_comparison_numbers_are_verbatim():
    """表 7：91.2% vs 87.5%，夜间 +8.3pp，高速 −1.2pp，评测集「城区 NOA 评测集 v5」。"""
    assert C.MODEL_COMPARE_DEMO_PASS_RATE == 0.912
    assert C.MODEL_COMPARE_DEMO_BASELINE_PASS_RATE == 0.875
    assert C.MODEL_COMPARE_DEMO_NIGHT_GAIN_PP == 8.3
    assert C.MODEL_COMPARE_DEMO_HIGHWAY_REGRESSION_PP == -1.2
    assert C.MODEL_COMPARE_DEMO_MODEL_VERSION == "v3.3"
    assert C.MODEL_COMPARE_DEMO_BASELINE_VERSION == "v3.2"
    assert C.MODEL_COMPARE_DEMO_DATASET_NAME == "城区 NOA 评测集 v5"
    # 高速是**回归**（负号不能丢）——正负号本身就是「带病上车」的判据
    assert C.MODEL_COMPARE_DEMO_HIGHWAY_REGRESSION_PP < 0 < C.MODEL_COMPARE_DEMO_NIGHT_GAIN_PP


def test_trigger_heatmap_numbers_are_verbatim():
    """表 9：AEB 误触发周环比上升 45%，热点是「城区晚高峰路口」，热力等级 1~5。"""
    assert C.TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE == 0.45
    assert C.TRIGGER_HEATMAP_DEMO_HOTSPOT == "城区晚高峰路口"
    assert (C.TRIGGER_HEAT_LEVEL_MIN, C.TRIGGER_HEAT_LEVEL_MAX) == (1, 5)
    # 「周环比」与表 8 的「发布后一周」是同一个 7 天口径
    assert C.TRIGGER_WOW_WINDOW_DAYS == C.OTA_POST_RELEASE_OBSERVE_DAYS == 7


def test_asset_catalog_and_dashboard_numbers_are_verbatim():
    """表 6 与表 1：12 次引用 / 4.6 分 / 3 个低分资产；216 小时 → 96 小时。"""
    assert C.ASSET_DEMO_REF_COUNT_90D == 12
    assert C.ASSET_DEMO_QUALITY_SCORE == 4.6
    assert C.ASSET_DEMO_IDLE_ASSET_COUNT == 3
    assert C.ASSET_QUALITY_SCORE_MAX == 5.0
    assert C.ASSET_HOT_WINDOW_DAYS == 90
    assert C.CLOSED_LOOP_BASELINE_HOURS == 216
    assert C.CLOSED_LOOP_TARGET_HOURS == 96
    assert C.CLOSED_LOOP_OPTIMIZATION_WEEKS == 1
    assert C.LAKEHOUSE_STORAGE_PB == 1.8


def test_matrix_scale_numbers_are_verbatim():
    """[S1-全景] 四：89 张表 = ODS 33 + DWD 28 + DWS 14 + ADS 11，另有 3 张血缘关系表。"""
    assert C.LAKE_TABLE_COUNT_ODS == 33
    assert C.LAKE_TABLE_COUNT_DWD == 28
    assert C.LAKE_TABLE_COUNT_DWS == 14
    assert C.LAKE_TABLE_COUNT_ADS == 11
    assert C.LAKE_LINEAGE_RELATION_TABLE_COUNT == 3
    assert C.LAKE_TABLE_COUNT_TOTAL == 89
    # 原文自己的四层加总就是 86，加上 3 张血缘关系表才是 89——两处口径要能对上
    four_layers = (
        C.LAKE_TABLE_COUNT_ODS
        + C.LAKE_TABLE_COUNT_DWD
        + C.LAKE_TABLE_COUNT_DWS
        + C.LAKE_TABLE_COUNT_ADS
    )
    assert four_layers + C.LAKE_LINEAGE_RELATION_TABLE_COUNT == C.LAKE_TABLE_COUNT_TOTAL
    assert C.LAKE_TABLE_COUNT_HEADLINE == 79
    assert C.ADS_TABLE_COUNT == C.LAKE_TABLE_COUNT_ADS == len(P.PRODUCTS) == 11
    assert C.ADS_THEME_COUNT == C.ADS_SERVED_PLATFORM_COUNT == 6
    assert C.CLOSED_LOOP_SERVICE_COUNT == len(P.ClosedLoopService) == 6
    assert C.CORE_SCENARIO_COUNT == 6
    assert C.CLOSED_LOOP_STAGE_COUNT == 8
    assert C.DATA_DOMAIN_COUNT == 11
    assert C.APPLICATION_PLATFORM_COUNT == 9


def test_tesla_mileage_constant_does_not_inflate_the_unit_by_ten_times():
    """「167 亿公里」是 10⁸ 量级，不是 billion——名字把量级说大 10 倍就等于改了原文。

    这条曾经写成 ``INDUSTRY_TESLA_FSD_BILLION_KM = 167.0``：值是对的，
    但任何照着名字换算的人都会得出 1670 亿公里。
    """
    assert C.INDUSTRY_TESLA_FSD_HUNDRED_MILLION_KM == 167.0
    assert C.INDUSTRY_TESLA_FSD_KM == 167.0 * 1e8 == 1.67e10
    assert not hasattr(C, "INDUSTRY_TESLA_FSD_BILLION_KM")


# --------------------------------------------------------------------------- 出处标注纪律

_CONST_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*:\s*Final")


def _annotations() -> dict[str, str]:
    """{常量名: 它上面那一段注释}。

    一段注释可以罩住紧挨着的一串常量（``GEO_LAT_RANGE`` / ``GEO_LON_RANGE``、
    ``LAKE_TABLE_COUNT_*`` 都是这么写的），所以注释块要向下延续，
    直到遇到空行或别的语句为止。
    """
    notes: dict[str, str] = {}
    pending: list[str] = []
    carried = ""
    previous_was_constant = False
    for line in inspect.getsource(C).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if previous_was_constant:
                pending = []
                carried = ""
            pending.append(stripped)
            previous_was_constant = False
            continue
        match = _CONST_LINE.match(line)
        if match:
            if pending:
                carried = "\n".join(pending)
            notes[match.group(1)] = carried
            previous_was_constant = True
            continue
        # 空行与其他语句（import / __all__ / docstring 结尾…）都截断注释块的作用范围
        pending = []
        carried = ""
        previous_was_constant = False
    return notes


#: 这些数字**是原文白纸黑字给的**，谁都不许把它们标成「本项目设计」。
_SOURCE_GIVEN = (
    "OTA_DEMO_SUCCESS_RATE",
    "OTA_DEMO_TRIGGER_GROWTH_RATE",
    "OTA_DEMO_SAFETY_ISSUE_COUNT",
    "OTA_DEMO_GREY_VEHICLE_COUNT",
    "OTA_POST_RELEASE_OBSERVE_DAYS",
    "HARD_CASE_DEMO_TOTAL_COUNT",
    "HARD_CASE_DEMO_ADOPTED_COUNT",
    "HARD_CASE_DEMO_ADOPTION_RATE",
    "TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE",
    "SCENE_LIBRARY_TAG_COUNT",
    "CLOSED_LOOP_BASELINE_HOURS",
    "CLOSED_LOOP_TARGET_HOURS",
    "MODEL_COMPARE_DEMO_PASS_RATE",
    "ASSET_DEMO_QUALITY_SCORE",
)

#: 这些是原文**没给**、本项目补的，必须带 ⚠️ 标注——否则照着它调参的人会理直气壮改口径。
_PROJECT_DESIGNED = (
    "BOTTLENECK_DURATION_RATIO_THRESHOLD",
    "BOTTLENECK_MOM_DETERIORATION_THRESHOLD",
    "MODEL_REGRESSION_TOLERANCE_PP_DEFAULT",
    "OTA_GATE_FLOAT_EPSILON",
    "TRIGGER_WOW_ANOMALY_THRESHOLD",
    "TRIGGER_HEAT_LEVEL_THRESHOLDS",
    "GEO_GRID_PRECISION_DEGREES",
    "ASSET_LOW_QUALITY_SCORE_THRESHOLD",
    "ASSET_IDLE_DAYS_THRESHOLD",
    "MINING_TAG_COVERAGE_WARN_THRESHOLD",
    "MINING_TAG_PENDING_REVIEW_WARN_COUNT",
    "ADS_QUERY_SLO_MS",
    "ADS_QUERY_DEFAULT_LIMIT",
    "ADS_QUERY_MAX_LIMIT",
    "ADS_AGGREGATION_MAX_ROWS",
    "GATEWAY_DEFAULT_QPS",
)


def test_source_given_numbers_are_not_labelled_as_project_design():
    notes = _annotations()
    for name in _SOURCE_GIVEN:
        assert name in notes, f"{name} 没有 `#:` 出处注释"
        assert "原文未明确" not in notes[name], (
            f"{name} 是原文给的数字，注释却写「原文未明确」——这等于把原文口径降格成拍脑袋值"
        )
        assert "[S1-" in notes[name], f"{name} 的注释没标出处章节"


def test_project_designed_numbers_carry_the_warning_marker():
    notes = _annotations()
    for name in _PROJECT_DESIGNED:
        assert name in notes, f"{name} 没有 `#:` 出处注释"
        assert "⚠️ 原文未明确，本项目设计：" in notes[name], (
            f"{name} 是本项目补的数字，必须以「⚠️ 原文未明确，本项目设计：」开头说明，"
            f"不能冒充原文方案"
        )


def test_every_constant_carries_a_provenance_note():
    """11 张表的口径全靠这一个模块兜底，没注释的常量等于没出处。"""
    notes = _annotations()
    public = [n for n in dir(C) if n.isupper() and not n.startswith("_")]
    missing = [n for n in public if n not in notes]
    assert missing == [], f"这些常量没有 `#:` 出处注释：{missing}"


# --------------------------------------------------------------------------- 剩余原文数字

#: [S1-全景] 第一章与第十章的行业背景数字——大盘文案与对标视图会直接引用它们。
INDUSTRY_FIGURES: tuple[tuple[str, object], ...] = (
    ("INDUSTRY_L4_DAILY_RAW_DATA_TB", 10),  # L4 公司每天产生原始数据超过 10TB
    ("INDUSTRY_DATA_UTILIZATION_RATIO", 0.02),  # 真正用于模型迭代的不到 2%
    ("INDUSTRY_TARGETED_KM_PER_GAIN", 10_000_000),  # 每增加 1000 万公里针对性场景数据
    ("INDUSTRY_TAKEOVER_DROP_MIN", 0.15),  # 接管率可降低 15%~25%
    ("INDUSTRY_TAKEOVER_DROP_MAX", 0.25),
    ("INDUSTRY_TESLA_AUTO_LABEL_RATE", 0.95),  # 特斯拉标注自动化率约 95%
    ("INDUSTRY_TESLA_ITERATION_WEEKS", 2),  # 迭代周期约 2 周
    ("INDUSTRY_XPENG_MILEAGE_RATIO", 0.50),  # 小鹏智驾里程占比破 50%
    ("INDUSTRY_XPENG_TAKEOVER_DROP", 0.26),  # 之后百公里接管降 26%
    ("DATA_SOVEREIGNTY_DOMESTIC_RATIO", 1.00),  # 国内采集数据 100% 境内存储
    ("SCENARIO_RAMP_BADCASE_RATE", 0.008),  # 高速匝道 Badcase 率 0.8%
    ("SCENARIO_RAMP_BASELINE_BADCASE_RATE", 0.003),  # 高于基线的 0.3%
    ("SCENE_GAP_TUNNEL_BACKLIT_COVERAGE_RATE", 0.002),  # 隧道内逆光+大车遮挡覆盖率 0.2%
    ("TRIGGER_DEMO_MONTHLY_TAKEOVER_COUNT", 1_200),  # 夜间无灯路口右转一个月触发 1,200 次接管
    ("PRODUCTION_CHAIN_STAGE_COUNT", 14),  # 采集 → … → 交付共 14 个环节
    ("SCENARIO_BATCH_CLIP_COUNT", 3_200),  # 某项目批次 3,200 条 clip 完成上云
    ("SCENARIO_BLOCKED_CLIP_COUNT", 120),  # 其中 120 条停留超 48 小时
    ("PRODUCTION_BLOCKED_ALERT_HOURS", 48),
)

#: [S1-全景] 第八章③存储生命周期——表 10 的口径全部引用这一组。
STORAGE_FIGURES: tuple[tuple[str, object], ...] = (
    ("NAS_PRICE_MULTIPLIER_MIN", 8),  # NAS/CPFS 单价约为 OSS 标准存储的 8~10 倍
    ("NAS_PRICE_MULTIPLIER_MAX", 10),
    ("OSS_IA_COST_MULTIPLIER", 0.5),  # OSS 低频约 0.5x 成本
    ("OSS_ARCHIVE_COST_MULTIPLIER", 0.15),  # OSS 归档约 0.15x 成本
    ("ARCHIVE_RESTORE_MAX_HOURS", 4),  # 归档取回 ≤4 小时
    ("HOT_TIER_BUFFER_DAYS", 7),  # 训练结束 7 天缓冲后淘汰
    ("WARM_TIER_DAYS", 30),  # 创建 30 天内，或 30 天内有访问
    ("COLD_TIER_NO_ACCESS_DAYS", 90),  # 连续 90 天无访问
    ("ARCHIVE_TIER_NO_ACCESS_DAYS", 180),  # 连续 180 天无访问
    ("RETENTION_DAYS", 365),  # 过 365 天保留期
    ("STORAGE_COST_DEMO_PREHEAT_DAYS", 9),  # 预热上 NAS 仅占 9 天
    ("NAS_PEAK_USAGE_ALERT_THRESHOLD", 0.80),  # NAS 峰值使用率 >80% 触发水位淘汰
)

#: [S1-全景] 第七章：向量检索的四个字面量，与 vector 子系统两处登记必须一致。
VECTOR_FIGURES: tuple[tuple[str, object], ...] = (
    ("HNSW_M", 16),
    ("HNSW_EF_CONSTRUCTION", 200),
    ("HNSW_METRIC", "cosine"),
    ("VECTOR_SEARCH_P95_LATENCY_SECONDS", 2.0),
)


@pytest.mark.parametrize(("name", "value"), [*INDUSTRY_FIGURES, *STORAGE_FIGURES, *VECTOR_FIGURES])
def test_the_remaining_source_numbers_are_registered_verbatim(name: str, value: object):
    """原文里出现过的每个数字都要在 constants 里逐字落地——这一批是剩下的。"""
    assert getattr(C, name) == value


def test_project_designed_operational_knobs_hold_their_documented_values():
    """原文没给的运维旋钮：值本身是本项目定的，但彼此的关系有硬约束，得钉住。"""
    # ADS 内表是「毫秒级直查」负载：10 秒还没回来说明选错了路（该走 Paimon 外部表）
    assert C.ADS_QUERY_TIMEOUT_SECONDS == 10
    assert C.ADS_QUERY_SLO_MS == 200
    assert C.ADS_QUERY_SLO_MS / 1000 < C.ADS_QUERY_TIMEOUT_SECONDS
    # T+1 数据一天只变一次，60 秒缓存削掉大屏轮询压力又不至于长时间看不到重刷结果
    assert C.ADS_RESULT_CACHE_TTL_SECONDS == 60
    # 桶容量必须大于速率，否则大屏首屏并发拉十几张表就会被自己限流
    assert C.GATEWAY_BURST_CAPACITY == 100
    assert C.GATEWAY_BURST_CAPACITY > C.GATEWAY_DEFAULT_QPS == 50
    assert C.GATEWAY_AUDIT_RING_SIZE == 1_000
    # 聚合翻页上限必须是单页上限的整数倍以上，否则第一页就触顶
    assert C.ADS_AGGREGATION_MAX_ROWS > C.ADS_QUERY_MAX_LIMIT > C.ADS_QUERY_DEFAULT_LIMIT


def test_lifecycle_tier_labels_are_not_the_lifecycle_stage_value_domain():
    """看板文案用的「五级档位」不是 ``lifecycle_stage`` 字段的取值域——别拿它校验字段。

    档位五值（热/温/冷/归档/删除）是 [S1-全景] 八③ 分层表的说法；
    字段取值域是六值（删除档拆成待删与已删），权威定义在 lifecycle.tiers.LifecycleStage。
    两者混用会让「已删」这类合法值被判成非法。
    """
    from adas_lakehouse.lifecycle.tiers import LifecycleStage

    assert C.STORAGE_LIFECYCLE_TIERS == ("hot", "warm", "cold", "archive", "delete")
    assert C.STORAGE_LIFECYCLE_STAGES is C.STORAGE_LIFECYCLE_TIERS  # 旧名只是别名
    field_domain = tuple(s.value for s in LifecycleStage)
    assert field_domain == ("hot", "warm", "cold", "archive", "pending_delete", "deleted")
    assert set(C.STORAGE_LIFECYCLE_TIERS) != set(field_domain)
    assert "delete" not in field_domain


def test_no_registered_constant_escapes_this_file_unasserted():
    """★覆盖闸★：constants 里每个公开常量，本文件至少要碰一次。

    这个模块的全部价值就是「原文每个数字的唯一出处」。一个没人断言的常量，
    被人手滑改成「合理值」也不会红——那它就不再是出处，只是一句注释。
    新增常量时请一并在本文件里给它一条断言。
    """
    text = inspect.getsource(
        inspect.getmodule(test_no_registered_constant_escapes_this_file_unasserted)
    )
    public = [n for n in dir(C) if n.isupper() and not n.startswith("_")]
    untouched = [n for n in public if re.search(rf"\b{re.escape(n)}\b", text) is None]
    assert untouched == [], f"这些常量没有任何断言盯着：{untouched}"


# ===========================================================================
# 二、11 张 ADS 表的产品矩阵（判据 A + B）
# ===========================================================================

#: [S1-05] 第二章全景表，逐字抄下来：序 / 表名 / 中文名 / 主要服务对象。
SOURCE_MATRIX: tuple[tuple[int, str, str, str], ...] = (
    (1, "ads_closed_loop_dashboard", "闭环大盘指标表", "监控大屏 · 数据管理平台"),
    (2, "ads_production_bottleneck_analysis", "产线瓶颈分析表", "数据管理平台 · 监控大屏"),
    (3, "ads_badcase_root_cause_distribution", "Badcase 根因分布表", "问题分析平台 · 评测平台"),
    (4, "ads_scene_library_summary", "场景库汇总表", "数据挖掘平台 · 数据管理平台"),
    (5, "ads_hard_case_library", "难例库表", "训练平台 · 数据管理平台"),
    (6, "ads_data_asset_catalog", "数据资产目录表", "数据管理平台"),
    (7, "ads_model_version_comparison", "模型版本对比表", "评测平台 · 训练平台"),
    (8, "ads_ota_deployment_summary", "OTA 部署汇总表", "监控大屏 · 问题分析平台"),
    (9, "ads_trigger_heatmap", "触发事件热力图表", "问题分析平台 · 数据挖掘平台"),
    (10, "ads_storage_cost_dashboard", "存储成本看板表", "监控大屏 · 数据管理平台"),
    (11, "ads_mining_tag_dashboard", "挖掘标签分布看板表", "监控大屏 · 数据管理平台"),
)


def test_eleven_products_match_the_source_matrix_row_by_row():
    """★11 张表★：序号、表名、中文名、主要服务对象——与原文第二章表格逐行逐字一致。"""
    assert len(P.PRODUCTS) == 11
    for (ordinal, table, title, serves), product in zip(SOURCE_MATRIX, P.PRODUCTS, strict=True):
        assert product.ordinal == ordinal
        assert product.table == table
        assert product.title_cn == title
        assert product.serves_cn == serves, f"{table} 的主要服务对象与原文不符"


def test_six_themes_group_the_tables_exactly_as_the_source_says():
    """[S1-05] 二：闭环健康度（1、2）、数据质量资产（3、4、5）、资产运营（6）、
    模型迭代与上车（7、8、9）、成本治理（10）、挖掘运营（11）。"""
    expected = {
        P.AdsTheme.CLOSED_LOOP_HEALTH: (1, 2),
        P.AdsTheme.DATA_QUALITY_ASSET: (3, 4, 5),
        P.AdsTheme.ASSET_OPERATION: (6,),
        P.AdsTheme.MODEL_ITERATION_ROLLOUT: (7, 8, 9),
        P.AdsTheme.COST_GOVERNANCE: (10,),
        P.AdsTheme.MINING_OPERATION: (11,),
    }
    assert len(expected) == C.ADS_THEME_COUNT == 6
    for theme, ordinals in expected.items():
        assert tuple(p.ordinal for p in P.products_by_theme(theme)) == ordinals


def test_the_eleven_tables_are_served_by_exactly_six_platforms():
    """[S1-05] 二图注「11 张 ADS 表：六大业务主题 × 六大业务平台」。"""
    served = {platform for product in P.PRODUCTS for platform in product.serves}
    assert len(served) == C.ADS_SERVED_PLATFORM_COUNT == 6
    # 9 大平台里没被 ADS 直接服务的三个：标注 / 仿真 / 车云
    untouched = set(P.BusinessPlatform) - served
    assert {p.name_cn for p in untouched} == {"标注平台", "仿真平台", "车云平台"}


def test_products_agree_with_the_catalog_registry():
    """产品矩阵 vs catalog 注册表：表名、主键、日期列、数据域，一处漂移都不放过。"""
    assert S.verify_against_catalog() == {}
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.domains import Layer

    assert {t.name for t in registry.by_layer(Layer.ADS)} == set(P.ADS_TABLE_NAMES)


def test_every_source_table_of_every_product_is_registered():
    """「上游从哪来」不能是文学描述：source_tables 里每张表都要真的在注册表里。"""
    from adas_lakehouse.catalog import registry

    known = {t.name for t in registry.all_tables()}
    for product in P.PRODUCTS:
        assert product.source_tables, f"{product.table} 没说明它从哪几张表物化"
        for source in product.source_tables:
            assert source in known, f"{product.table} 的上游 {source!r} 未注册"


def test_unknown_table_is_rejected_by_name():
    with pytest.raises(UnknownTableError, match="11 张"):
        P.get_product("ads_made_up_table")


# ===========================================================================
# 三、★OTA 灰度三条件放行★（判据 C + D，本模块的重点）
# ===========================================================================


def test_the_source_case_passes_all_three_conditions():
    """原文案例：99.2% / +30% / 0 起 → 「确认全量推送」。"""
    decision = gate()
    assert decision.grey_passed is True
    assert decision.passed is True
    assert decision.decision == C.OTA_GATE_DECISION_PASS == "full_rollout"
    assert decision.blocked_by == ()
    assert decision.missing_inputs == ()
    assert "确认全量推送" in decision.summary_cn


def test_the_three_conditions_come_first_and_in_the_source_order():
    """三条件的顺序即原文并列的顺序：成功率 → 触发量环比 → 安全问题。"""
    decision = gate()
    assert len(decision.grey_conditions) == C.OTA_GATE_CONDITION_COUNT == 3
    assert [c.key for c in decision.grey_conditions] == [
        "deploy_success_rate",
        "post_release_trigger_growth_rate",
        "safety_issue_count",
    ]
    assert [c.name_cn for c in decision.grey_conditions] == [
        "升级成功率",
        "发布后一周回传触发量环比增长",
        "安全相关问题",
    ]
    # 每条判据都要能指回原文，审计里才解释得清「为什么卡住」
    for condition in decision.grey_conditions:
        assert "[S1-05] 第五章表 8" in condition.source_cn


def test_success_rate_boundary_9919_holds_and_992_passes():
    """★边界★：99.19% 不放行，99.2% 放行。

    比较号取 ``≥`` 的依据是原文自己：99.2% 这个**实测值**在原文里的结论就是
    「确认全量推送」，门槛必须把等号算进来，否则原文案例自己都过不了门。
    """
    held = gate(deploy_success_rate=0.9919)
    assert held.passed is False
    assert held.blocked_by == ("deploy_success_rate",)
    assert held.missing_inputs == ()  # 是「实测不达标」，不是「查不到」
    assert "不放行" in held.summary_cn

    assert gate(deploy_success_rate=0.992).passed is True
    # 只要还差一点就不放行，哪怕只差 1e-6
    assert gate(deploy_success_rate=0.992 - 1e-6).passed is False


def test_the_float_epsilon_only_absorbs_representation_error():
    """容差 1e-9 只抵消 IEEE-754 表示误差，不放宽门槛。

    折算成百分点是 1e-7 pp——99.1999% 这种「差一点」照样拦住。
    """
    assert C.OTA_GATE_FLOAT_EPSILON == 1e-9
    assert gate(deploy_success_rate=0.991999).passed is False
    # 496/500 在二进制里与字面量 0.992 同值，案例不能因为浮点表示被误伤
    assert gate(deploy_success_rate=496 / 500).passed is True


def test_trigger_growth_boundary_299_holds_and_30_passes():
    """★边界★：环比 +29.9% 不放行，+30% 放行；下降（负增长）更不放行。"""
    assert gate(post_release_trigger_growth_rate=0.299).passed is False
    assert gate(post_release_trigger_growth_rate=0.30).passed is True
    assert gate(post_release_trigger_growth_rate=0.31).passed is True

    dropped = gate(post_release_trigger_growth_rate=-0.10)
    assert dropped.passed is False
    assert dropped.blocked_by == ("post_release_trigger_growth_rate",)


def test_one_safety_issue_blocks_no_matter_how_good_everything_else_is():
    """★边界★：安全相关问题 1 起 → 无论其他指标多漂亮都不放行。"""
    blocked = gate(
        deploy_success_rate=0.9999,
        post_release_trigger_growth_rate=3.0,
        safety_issue_count=1,
    )
    assert blocked.passed is False
    assert blocked.grey_passed is False
    assert blocked.blocked_by == ("safety_issue_count",)
    assert blocked.decision == C.OTA_GATE_DECISION_HOLD == "hold"
    # 另外两条确实是通过的——被卡住的只有安全这一条，归因不能糊成一团
    passed_keys = [c.key for c in blocked.conditions if c.passed]
    assert passed_keys == ["deploy_success_rate", "post_release_trigger_growth_rate"]


def test_the_three_conditions_are_an_AND_over_every_combination():
    """★与关系★：三条件 2³ = 8 种组合，只有全中那一种才放行。"""
    good = {
        "deploy_success_rate": (0.992, 0.9919),
        "post_release_trigger_growth_rate": (0.30, 0.29),
        "safety_issue_count": (0, 1),
    }
    passing = []
    for ok_rate in (True, False):
        for ok_growth in (True, False):
            for ok_safety in (True, False):
                decision = gate(
                    deploy_success_rate=good["deploy_success_rate"][0 if ok_rate else 1],
                    post_release_trigger_growth_rate=(
                        good["post_release_trigger_growth_rate"][0 if ok_growth else 1]
                    ),
                    safety_issue_count=good["safety_issue_count"][0 if ok_safety else 1],
                )
                assert decision.grey_passed is (ok_rate and ok_growth and ok_safety)
                if decision.grey_passed:
                    passing.append((ok_rate, ok_growth, ok_safety))
    assert passing == [(True, True, True)], "8 种组合里只有三条全中的那一种能放行"


@pytest.mark.parametrize(
    "missing_key",
    ["deploy_success_rate", "post_release_trigger_growth_rate", "safety_issue_count"],
)
def test_a_missing_input_holds_the_release_and_is_reported_as_missing(missing_key: str):
    """缺数据一律判不通过，且与「实测不达标」分开归因——绝不把「查不到」当「没问题」。"""
    decision = gate(**{missing_key: None})
    assert decision.passed is False
    assert decision.blocked_by == (missing_key,)
    assert decision.missing_inputs == (missing_key,)
    detail = next(c.detail_cn for c in decision.conditions if c.key == missing_key)
    assert "判据数据缺失" in detail


def test_negative_safety_count_raises_instead_of_sneaking_through():
    """−1 起安全问题会让「≤ 0」凭空成立——这是最典型的漏放路径，必须报错。"""
    with pytest.raises(ValueError, match="为负"):
        gate(safety_issue_count=-1)


@pytest.mark.parametrize("bad_rate", [-0.01, 1.01, 99.2])
def test_out_of_range_success_rate_raises(bad_rate: float):
    """成功率是比率列，99.2（而不是 0.992）这种单位写错的值不能被当成「远超门槛」。"""
    with pytest.raises(ValueError, match="越界"):
        gate(deploy_success_rate=bad_rate)


def test_release_channel_must_be_one_of_the_three():
    assert gate(release_channel="grey").passed is True
    with pytest.raises(ValueError, match="发布通道"):
        gate(release_channel="canary")


def test_regression_check_adds_a_fourth_condition_that_can_block():
    """表 7 的回归项：夜间 +8.3pp 再漂亮，高速 −1.2pp 也要拦——「修复后再 OTA」。"""
    rows = [
        {
            "model_version": "v3.3",
            "baseline_model_version": "v3.2",
            "scene_type": "夜间",
            "pass_rate_diff_pp": 8.3,
            "regression_flag": False,
        },
        {
            "model_version": "v3.3",
            "baseline_model_version": "v3.2",
            "scene_type": "高速",
            "pass_rate_diff_pp": -1.2,
            "regression_flag": True,
        },
    ]
    check = SV.RegressionCheck.from_rows(rows)
    assert check is not None
    assert check.has_regression is True
    assert check.regression_scenes == ("高速",)
    assert check.worst_scene_type == "高速"
    assert check.worst_diff_pp == -1.2
    assert check.best_scene_type == "夜间"
    assert check.best_diff_pp == 8.3

    decision = gate(regression=check)
    assert decision.grey_passed is True, "表 8 的三条件本身是过的"
    assert decision.passed is False, "但带回归项不许上车"
    assert decision.blocked_by == ("evaluation_regression",)
    assert "修复后再 OTA" in next(
        c.detail_cn for c in decision.conditions if c.key == "evaluation_regression"
    )


def test_regression_check_passes_when_no_scene_regresses():
    rows = [
        {
            "model_version": "v3.3",
            "baseline_model_version": "v3.2",
            "scene_type": "夜间",
            "pass_rate_diff_pp": 8.3,
            "regression_flag": False,
        },
        {
            "model_version": "v3.3",
            "baseline_model_version": "v3.2",
            "scene_type": "城区",
            "pass_rate_diff_pp": 3.7,
            "regression_flag": False,
        },
    ]
    check = SV.RegressionCheck.from_rows(rows)
    assert check is not None and check.has_regression is False
    assert gate(regression=check).passed is True


def test_a_regression_flag_alone_blocks_even_without_a_diff_value():
    """列里只打了 regression_flag、没给差值时，也必须拦——缺判据不等于没问题。"""
    rows = [
        {
            "model_version": "v3.3",
            "baseline_model_version": "v3.2",
            "scene_type": "高速",
            "pass_rate_diff_pp": None,
            "regression_flag": True,
        },
    ]
    check = SV.RegressionCheck.from_rows(rows)
    assert check is not None and check.regression_scenes == ("高速",)
    assert gate(regression=check).passed is False


def test_no_evaluation_rows_means_no_conclusion_not_a_pass():
    assert SV.RegressionCheck.from_rows([]) is None


def test_require_evaluation_blocks_when_there_is_no_comparison_at_all():
    """[S1-全景] 一「仿真评测必须先于 OTA 部署——顺序不能乱」。"""
    decision = gate(regression=None, require_evaluation=True)
    assert decision.grey_passed is True
    assert decision.passed is False
    assert decision.missing_inputs == ("evaluation_regression",)
    assert "仿真评测必须先于 OTA 部署" in next(
        c.source_cn for c in decision.conditions if c.key == "evaluation_regression"
    )


def test_tolerance_pp_can_be_relaxed_per_project_but_defaults_to_zero():
    """默认容差 0.0 pp：通过率比基线低就算回归，宁可多拦一次也不放带病模型上车。"""
    assert C.MODEL_REGRESSION_TOLERANCE_PP_DEFAULT == 0.0
    rows = [
        {
            "model_version": "v3.3",
            "baseline_model_version": "v3.2",
            "scene_type": "高速",
            "pass_rate_diff_pp": -1.2,
            "regression_flag": False,
        },
    ]
    strict = SV.RegressionCheck.from_rows(rows)
    assert strict is not None and strict.has_regression is True

    lenient = SV.RegressionCheck.from_rows(rows, tolerance_pp=-2.0)
    assert lenient is not None and lenient.has_regression is False
    assert gate(regression=lenient, tolerance_pp=-2.0).passed is True


def test_as_dict_carries_every_condition_for_the_audit_trail():
    payload = gate(safety_issue_count=1).as_dict()
    assert payload["decision"] == "hold"
    assert payload["grey_passed"] is False
    assert payload["blocked_by"] == ["safety_issue_count"]
    assert len(payload["conditions"]) == 3
    thresholds = {c["key"]: c["threshold"] for c in payload["conditions"]}
    assert thresholds == {
        "deploy_success_rate": 0.992,
        "post_release_trigger_growth_rate": 0.30,
        "safety_issue_count": 0.0,
    }
    comparators = {c["key"]: c["comparator"] for c in payload["conditions"]}
    assert comparators == {
        "deploy_success_rate": ">=",
        "post_release_trigger_growth_rate": ">=",
        "safety_issue_count": "<=",
    }


# --------------------------------------------------------------------------- 端到端走服务层


def test_release_gate_end_to_end_reproduces_the_source_case(suite):
    """★端到端★：演示行源 → 表 8 取成功率 → 表 9 算环比 → 三条件 → 全量推送。"""
    decision = suite.model.ota_release_gate(
        ota_task_id=D.DEMO_OTA_TASK_ID, safety_issue_count=0, check_regression=False
    )
    actual = {c.key: c.actual for c in decision.grey_conditions}
    assert actual["deploy_success_rate"] == pytest.approx(0.992)
    assert actual["post_release_trigger_growth_rate"] == pytest.approx(0.30)
    assert actual["safety_issue_count"] == 0.0
    assert decision.decision == "full_rollout"
    assert decision.release_channel == "grey"


def test_release_gate_end_to_end_blocks_on_the_highway_regression(suite):
    """打开回归检查后，同一个任务因为高速场景 −1.2pp 被拦——这就是「避免带病上车」。"""
    decision = suite.model.ota_release_gate(
        ota_task_id=D.DEMO_OTA_TASK_ID, safety_issue_count=0, check_regression=True
    )
    assert decision.grey_passed is True
    assert decision.passed is False
    assert decision.blocked_by == ("evaluation_regression",)


def test_release_gate_without_a_safety_source_holds(suite):
    """表 8 没有「安全相关问题数」这一列：不传、也不注入端口 → 判不通过。"""
    decision = suite.model.ota_release_gate(ota_task_id=D.DEMO_OTA_TASK_ID, check_regression=False)
    assert decision.passed is False
    assert decision.missing_inputs == ("safety_issue_count",)


def test_release_gate_takes_the_safety_count_from_the_injected_port():
    """安全问题数由问题分析平台经 SafetyIssuePort 供给，窗口是发布日起 7 天。"""
    seen: dict[str, object] = {}

    class Port:
        def safety_issue_count(self, *, software_version, since, until, project_code=None):
            seen.update(
                software_version=software_version,
                since=since,
                until=until,
                project_code=project_code,
            )
            return 2

    suite = SV.ClosedLoopServiceSuite(
        Q.AdsQueryService(D.DemoRowSource()), safety_issue_port=Port()
    )
    decision = suite.model.ota_release_gate(ota_task_id=D.DEMO_OTA_TASK_ID, check_regression=False)
    assert decision.passed is False
    assert decision.blocked_by == ("safety_issue_count",)
    assert seen["since"] == D.DEMO_PUBLISH_DATE
    # 闭区间：发布日 + 6 天 = 一周
    assert seen["until"] == D.DEMO_PUBLISH_DATE + timedelta(days=6)
    assert (seen["until"] - seen["since"]).days + 1 == C.OTA_POST_RELEASE_OBSERVE_DAYS == 7


def test_release_gate_raises_for_an_unknown_ota_task(suite):
    with pytest.raises(AdsQueryError, match="不在 ads_ota_deployment_summary"):
        suite.model.ota_release_gate(ota_task_id="OTA-DOES-NOT-EXIST")


def test_release_gate_on_an_empty_lake_holds_everything(empty_suite):
    """物化还没跑：连任务行都取不到，报错而不是给一个「通过」。"""
    with pytest.raises(AdsQueryError):
        empty_suite.model.ota_release_gate(ota_task_id=D.DEMO_OTA_TASK_ID)


def test_post_release_trigger_growth_windows_are_two_adjacent_weeks(suite):
    """条件②的口径：本期 = [发布日, +6]，上期 = 紧邻的前 7 天，1000 → 1300 = +30%。"""
    growth = suite.trigger.post_release_trigger_growth(publish_date=D.DEMO_PUBLISH_DATE)
    assert growth.window_days == 7
    assert growth.current_window == (D.DEMO_PUBLISH_DATE, D.DEMO_PUBLISH_DATE + timedelta(days=6))
    assert growth.previous_window == (
        D.DEMO_PUBLISH_DATE - timedelta(days=7),
        D.DEMO_PUBLISH_DATE - timedelta(days=1),
    )
    # 两个窗口首尾相接，不重不漏
    assert growth.previous_window[1] + timedelta(days=1) == growth.current_window[0]
    assert growth.previous_count == D.DEMO_PREV_WEEK_TRIGGER_TOTAL == 1_000
    assert growth.current_count == D.DEMO_CURRENT_WEEK_TRIGGER_TOTAL == 1_300
    assert growth.rate == pytest.approx(0.30)
    assert growth.is_new_baseline is False


def test_trigger_growth_distinguishes_a_zero_baseline_from_missing_data():
    """上期为 0 与两期都没数据，在门禁上同样不放行，但归因必须分得开。"""
    window = (date(2026, 8, 28), date(2026, 9, 3))
    previous = (date(2026, 8, 21), date(2026, 8, 27))
    fresh = SV.TriggerGrowth(window, previous, current_count=1_300, previous_count=0, window_days=7)
    assert fresh.rate is None
    assert fresh.is_new_baseline is True

    nothing = SV.TriggerGrowth(window, previous, current_count=0, previous_count=0, window_days=7)
    assert nothing.rate is None
    assert nothing.is_new_baseline is False
    assert gate(post_release_trigger_growth_rate=fresh.rate).passed is False


def test_growth_rate_formula_matches_the_source_case():
    """环比 =（本期 − 上期）/ 上期；上期为 0 不返回 0，而是返回 None。"""
    assert SV.growth_rate(1_300, 1_000) == pytest.approx(0.30)
    assert SV.growth_rate(290, 200) == pytest.approx(0.45)
    assert SV.growth_rate(1_000, 0) is None
    assert SV.growth_rate(None, 1_000) is None
    assert SV.growth_rate(1_000, None) is None


def test_trigger_volume_rejects_an_inverted_window(suite):
    with pytest.raises(AdsQueryError, match="颠倒"):
        suite.trigger.trigger_volume(start=date(2026, 9, 3), end=date(2026, 8, 28))


def test_post_release_window_must_be_positive(suite):
    with pytest.raises(AdsQueryError, match="正天数"):
        suite.trigger.post_release_trigger_growth(publish_date=D.DEMO_PUBLISH_DATE, window_days=0)


# ===========================================================================
# 四、★地理网格热力图★（判据 C + D）
# ===========================================================================


def test_grid_precision_is_one_hundredth_of_a_degree():
    """网格粒度 0.01 度（纬向约 1.1 km），够表达「城区某个路口」这一粒度。"""
    assert C.GEO_GRID_PRECISION_DEGREES == 0.01
    assert C.GEO_GRID_ID_DECIMALS == 2
    assert geo.GRID_SIZE_DEGREES == C.GEO_GRID_PRECISION_DEGREES
    cell = geo.grid_center(geo.grid_id(31.2345, 121.4678))
    south, west, north, east = cell.bounds
    assert round(north - south, 10) == round(east - west, 10) == 0.01


def test_nearby_points_collapse_into_one_grid_and_far_ones_do_not():
    """同一个路口的两次触发必须落进同一格，隔一个网格的必须分开。"""
    here = geo.grid_id(31.2345, 121.4678)
    assert here == "31.23_121.47"
    assert geo.grid_id(31.2301, 121.4701) == here
    assert geo.grid_id(31.2449, 121.4678) != here
    assert geo.grid_center(here).contains(31.2301, 121.4701) is True
    assert geo.grid_center(here).contains(31.3012, 121.5034) is False


def test_grid_id_round_trips_through_parse_and_center():
    for lat, lon in ((31.23, 121.47), (-33.87, 151.21), (0.0, 0.0)):
        key = geo.grid_id(lat, lon)
        assert geo.parse_grid_id(key) == pytest.approx((lat, lon))
        cell = geo.grid_center(key)
        assert (cell.center_lat, cell.center_lon) == pytest.approx((lat, lon))
        assert cell.grid_id == key


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(90.1, 0.0), (-90.1, 0.0), (0.0, 180.1), (0.0, -180.1)],
)
def test_dirty_gps_is_refused_instead_of_becoming_a_fake_hotspot(lat: float, lon: float):
    """脏 GPS 不进热力图：宁可报错，也不在地图上画一个不存在的热点。"""
    with pytest.raises(ValueError, match="越界"):
        geo.grid_id(lat, lon)
    # 边界值本身是合法的
    assert geo.grid_id(90.0, 180.0)
    assert geo.grid_id(-90.0, -180.0)


def test_parse_grid_id_rejects_anything_this_module_did_not_encode():
    for bad in ("31.23", "31.23_121.47_0", "abc_def"):
        with pytest.raises(ValueError, match="不是网格键"):
            geo.parse_grid_id(bad)


def _sql_snap(value: float, step: Decimal, decimals: int) -> str:
    """按 Flink ``ROUND()`` 的语义（BigDecimal HALF_UP）模拟 SQL 侧的网格编码。"""
    quotient = (Decimal(str(value)) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return f"{quotient * step:.{decimals}f}"


@pytest.mark.parametrize(
    "lat",
    [31.2345, 31.225, 31.235, 31.245, 0.005, 0.015, 0.025, -0.005, -31.225, 22.0, 39.9042],
)
def test_python_and_flink_snap_the_same_way_including_exact_halves(lat: float):
    """★口径同源★：服务层与 Flink 批作业对「正好落在网格边界」的点必须分到同一格。

    内置 :func:`round` 是银行家舍入（逢半取偶），``round(3122.5)`` 得 3122；
    Flink 的 ``ROUND`` 走 ``BigDecimal.setScale(0, HALF_UP)``，得 3123。
    两边不一致时，纬度 31.225 的那一批触发会被劈成 31.22 和 31.23 两格，
    大屏上一个真热点被稀释成两个温点——这正是本项目改掉 ``round`` 的原因。
    """
    step = Decimal(str(C.GEO_GRID_PRECISION_DEGREES))
    expected_lat = _sql_snap(lat, step, C.GEO_GRID_ID_DECIMALS)
    assert geo.grid_id(lat, 121.4678).split(C.GEO_GRID_ID_SEPARATOR)[0] == expected_lat


def test_the_bankers_rounding_regression_is_pinned():
    """把上面那条回归钉死成一个具体数字，免得有人「顺手」换回内置 round。"""
    assert round(31.225 / 0.01) == 3122  # 银行家舍入的行为，作为对照
    assert geo.grid_id(31.225, 0.0) == "31.23_0.00"  # 我们要的是 HALF_UP
    assert geo.grid_id(-0.005, 0.0) == "-0.01_0.00"  # 负数也逢半远离零


def test_negative_zero_never_leaks_into_a_grid_key():
    """``-0.00`` 与 ``0.00`` 必须是同一个网格键，否则赤道/本初子午线附近会裂成两格。"""
    assert geo.grid_id(-0.004, -0.004) == "0.00_0.00"
    assert geo.grid_id(0.004, 0.004) == "0.00_0.00"


def test_grid_id_sql_carries_the_same_step_and_scale():
    sql = geo.grid_id_sql("t.gps_lat", "t.gps_lon")
    assert f"/ {C.GEO_GRID_PRECISION_DEGREES}" in sql
    assert "ROUND(" in sql
    # 定标 DECIMAL 不能省：CAST(31.2 AS STRING) 得 '31.2'，与 Python 的 '31.20' 对不上
    assert f"DECIMAL({2 + C.GEO_GRID_ID_DECIMALS}, {C.GEO_GRID_ID_DECIMALS})" in sql  # 纬度 ±90
    assert f"DECIMAL({3 + C.GEO_GRID_ID_DECIMALS}, {C.GEO_GRID_ID_DECIMALS})" in sql  # 经度 ±180
    assert f"'{C.GEO_GRID_ID_SEPARATOR}'" in sql


# --------------------------------------------------------------------------- 热力等级


@pytest.mark.parametrize(
    ("count", "level"),
    [
        (0, 1),
        (4, 1),  # < 5          → 1
        (5, 2),
        (19, 2),  # [5, 20)      → 2
        (20, 3),
        (49, 3),  # [20, 50)     → 3
        (50, 4),
        (99, 4),  # [50, 100)    → 4
        (100, 5),
        (10_000, 5),  # ≥ 100        → 5
    ],
)
def test_heat_level_buckets_are_closed_on_the_lower_bound(count: int, level: int):
    """五档热力等级，每档的下界闭合：5 进第 2 档、20 进第 3 档、100 进第 5 档。"""
    assert C.TRIGGER_HEAT_LEVEL_THRESHOLDS == (5, 20, 50, 100)
    assert geo.heat_level(count) == level


def test_heat_level_never_leaves_one_to_five():
    levels = {geo.heat_level(n) for n in range(0, 300)}
    assert min(levels) == C.TRIGGER_HEAT_LEVEL_MIN == 1
    assert max(levels) == C.TRIGGER_HEAT_LEVEL_MAX == 5
    assert geo.heat_level(10**9) == 5


def test_a_negative_trigger_count_is_an_upstream_bug_not_the_lowest_bucket():
    with pytest.raises(ValueError, match="不能为负"):
        geo.heat_level(-1)


def _eval_heat_case(sql: str, count: int) -> int:
    """把 :func:`geo.heat_level_sql` 渲染出的 CASE 当成判定表跑一遍。"""
    for threshold, level in re.findall(r"WHEN \S+ >= (\d+) THEN (\d+)", sql):
        if count >= int(threshold):
            return int(level)
    return int(re.search(r"ELSE (\d+) END", sql).group(1))


def test_flink_case_expression_and_python_agree_on_every_boundary():
    """★同源★：批作业物化出来的 heat_level 与服务层重算的必须逐档相同。"""
    sql = geo.heat_level_sql("g.trigger_cnt")
    for count in (0, 1, 4, 5, 6, 19, 20, 21, 49, 50, 51, 99, 100, 101, 5_000):
        assert _eval_heat_case(sql, count) == geo.heat_level(count), f"count={count} 两边不同档"
    # CASE 从高档往低档写，先命中先返回
    assert sql.index("WHEN g.trigger_cnt >= 100") < sql.index("WHEN g.trigger_cnt >= 5 ")


# --------------------------------------------------------------------------- 聚合口径


def test_heatmap_rows_carry_everything_the_map_needs_with_zero_join(suite):
    """零 JOIN：网格中心、城市名、道路类型、代表样本都打平在行内，前端 SELECT 即渲染。"""
    cells = suite.trigger.heatmap(stat_date=D.DEMO_LATEST_DATE)
    assert cells
    top = cells[0]
    assert top.geo_grid_id == D.DEMO_GRID_HOTSPOT
    assert top.city_name == "上海市"
    assert top.road_type == "urban_intersection"
    assert top.grid_center_lat is not None and top.grid_center_lon is not None
    assert top.sample_data_id, "点热点要能下钻到原始片段，代表样本 data_id 不能空"
    # 行里存的等级与服务层重算的必须一致，否则「大屏取色和明细对不上」
    assert all(c.heat_level_matches for c in cells)
    assert top.heat_level == top.expected_heat_level == geo.heat_level(top.trigger_cnt)
    assert top.cell.grid_id == top.geo_grid_id


def test_heatmap_is_sorted_by_trigger_count_descending(suite):
    counts = [c.trigger_cnt for c in suite.trigger.heatmap(stat_date=D.DEMO_LATEST_DATE)]
    assert counts == sorted(counts, reverse=True)


def test_min_heat_level_out_of_range_raises_instead_of_silently_emptying(suite):
    """传 9 会静默返回空、传 0 会静默返回全量——大屏两种都看不出问题，所以报错。"""
    with pytest.raises(AdsQueryError, match="热力等级"):
        suite.trigger.heatmap(stat_date=D.DEMO_LATEST_DATE, min_heat_level=0)
    with pytest.raises(AdsQueryError, match="热力等级"):
        suite.trigger.heatmap(stat_date=D.DEMO_LATEST_DATE, min_heat_level=6)
    assert suite.trigger.heatmap(stat_date=D.DEMO_LATEST_DATE, min_heat_level=1)


def test_grid_rollup_sums_counts_and_recomputes_the_level_from_the_total():
    """★聚合口径★：各项求和，热力等级按**合并后的总量**重算，不是把各类型等级相加。"""

    def cell(trigger_type: str, count: int) -> SV.HeatCell:
        return SV.HeatCell.from_row(
            {
                "stat_date": date(2026, 9, 3),
                "project_code": "NOA-CITY",
                "geo_grid_id": "31.23_121.47",
                "trigger_type": trigger_type,
                "trigger_cnt": count,
                "heat_level": geo.heat_level(count),
                "vehicle_cnt": count // 2,
                "hard_case_cnt": count // 5,
                "grid_center_lat": 31.23,
                "grid_center_lon": 121.47,
                "city_name": "上海市",
                "road_type": "urban_intersection",
                "top_scene_tag": "城区晚高峰路口",
                "sample_data_id": f"COLLECT_BP_20260903120000_{trigger_type[:2]}",
            }
        )

    cells = [cell("AEB误触发", 30), cell("接管", 40)]
    assert [c.heat_level for c in cells] == [3, 3]  # 单看每类都是第 3 档

    hotspots = SV.TriggerMiningClosedLoopService.grid_rollup(cells)
    assert len(hotspots) == 1
    spot = hotspots[0]
    assert spot.trigger_cnt == 70
    assert spot.vehicle_cnt == 15 + 20
    assert spot.hard_case_cnt == 6 + 8
    assert spot.heat_level == geo.heat_level(70) == 4, "合并后越档，不能停在第 3 档"
    assert spot.heat_level != 3 + 3, "等级不是相加出来的"
    assert spot.top_trigger_type == "接管"  # 次数最多的那一类做代表
    assert spot.trigger_types == ("AEB误触发", "接管")


def test_top_hotspots_answers_where_the_triggers_concentrate(suite):
    """原文案例：「热力图显示触发集中在城区晚高峰路口」。"""
    hotspots = suite.trigger.top_hotspots(stat_date=D.DEMO_LATEST_DATE, top_n=2)
    assert hotspots[0].geo_grid_id == D.DEMO_GRID_HOTSPOT
    assert hotspots[0].top_scene_tag == C.TRIGGER_HEATMAP_DEMO_HOTSPOT == "城区晚高峰路口"
    assert hotspots[0].road_type == "urban_intersection"
    # 排序按合并后的总量，郊区那格排在后面
    assert hotspots[0].trigger_cnt > hotspots[1].trigger_cnt
    assert hotspots[1].geo_grid_id == D.DEMO_GRID_SUBURB


def test_grid_rollup_of_nothing_is_nothing():
    assert SV.TriggerMiningClosedLoopService.grid_rollup([]) == []


# --------------------------------------------------------------------------- 周环比 45%


def test_the_45_percent_aeb_anomaly_is_caught_and_quiet_grids_are_not(suite):
    """★原文案例★：某区域「AEB 误触发」周环比上升 45% → 进异常清单。

    对照组：郊区「接管」上周 100 → 本周 110（+10%），低于告警线，不许误报。
    """
    anomalies = suite.trigger.wow_anomalies(stat_date=D.DEMO_LATEST_DATE)
    by_key = {(a.geo_grid_id, a.trigger_type): a for a in anomalies}

    aeb = by_key[(D.DEMO_GRID_HOTSPOT, D.DEMO_TRIGGER_TYPE_AEB)]
    assert aeb.previous_count == D.DEMO_PREV_WEEK_AEB_HOTSPOT == 200
    assert aeb.current_count == D.DEMO_CURRENT_WEEK_AEB_HOTSPOT == 290
    assert aeb.wow_rate == pytest.approx(C.TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE) == pytest.approx(0.45)
    assert aeb.is_new_hotspot is False
    assert aeb.threshold == C.TRIGGER_WOW_ANOMALY_THRESHOLD == 0.30
    assert "45%" in aeb.reason_cn
    # 45% 的案例必然被捞出来：告警线 30% 严格低于案例值
    assert C.TRIGGER_WOW_ANOMALY_THRESHOLD < C.TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE

    assert (D.DEMO_GRID_SUBURB, D.DEMO_TRIGGER_TYPE_TAKEOVER) not in by_key, "+10% 不该告警"


def test_a_grid_that_was_zero_last_week_is_flagged_as_a_new_hotspot(suite):
    """上周 0、本周 20：算不出环比，但这恰恰是最该看的一类——单列成「新增热点」。"""
    anomalies = suite.trigger.wow_anomalies(stat_date=D.DEMO_LATEST_DATE)
    fresh = next(a for a in anomalies if a.is_new_hotspot)
    assert fresh.geo_grid_id == D.DEMO_GRID_SUBURB
    assert fresh.trigger_type == D.DEMO_TRIGGER_TYPE_AEB
    assert fresh.previous_count == 0
    assert fresh.current_count == D.DEMO_CURRENT_WEEK_SUBURB_AEB == 20
    assert fresh.wow_rate is None
    assert "新增热点" in fresh.reason_cn
    # 新增热点排在最前——运维最该先看它
    assert anomalies[0].is_new_hotspot is True


def test_new_hotspots_can_be_switched_off(suite):
    anomalies = suite.trigger.wow_anomalies(
        stat_date=D.DEMO_LATEST_DATE, include_new_hotspots=False
    )
    assert anomalies
    assert all(a.is_new_hotspot is False for a in anomalies)


def test_wow_anomaly_windows_are_two_adjacent_seven_day_windows(suite):
    """周环比窗口：本周 = [T−6, T]，上周 = 紧邻的前 7 天，与表 8 同一个 7 天口径。

    把窗口缩到 1 天来验窗口真的在起作用（告警线放到 0 以便观察每一格）：
    演示数据把周合计平摊到每天、余数落最后一天，所以只比 T 日与 T−1 日时，
    AEB 热点格的本期计数必须正好是「日均 + 余数」，而不是整周的 290。
    """

    def aeb(window_days: int) -> SV.WowAnomaly:
        anomalies = suite.trigger.wow_anomalies(
            stat_date=D.DEMO_LATEST_DATE, window_days=window_days, threshold=0.0
        )
        return next(
            a
            for a in anomalies
            if (a.geo_grid_id, a.trigger_type) == (D.DEMO_GRID_HOTSPOT, D.DEMO_TRIGGER_TYPE_AEB)
        )

    per_day, remainder = divmod(D.DEMO_CURRENT_WEEK_AEB_HOTSPOT, C.TRIGGER_WOW_WINDOW_DAYS)
    narrow = aeb(1)
    assert narrow.current_count == per_day + remainder
    assert narrow.previous_count == per_day

    wide = aeb(C.TRIGGER_WOW_WINDOW_DAYS)
    assert wide.current_count == D.DEMO_CURRENT_WEEK_AEB_HOTSPOT == 290
    assert wide.previous_count == D.DEMO_PREV_WEEK_AEB_HOTSPOT == 200
    assert wide.wow_rate == pytest.approx(0.45)
    # 窗口宽度真的改变了口径：1 天窗口的涨幅远低于整周的 45%，不会误报成大涨
    assert narrow.wow_rate is not None and narrow.wow_rate < C.TRIGGER_WOW_ANOMALY_THRESHOLD

    with pytest.raises(AdsQueryError, match="正天数"):
        suite.trigger.wow_anomalies(stat_date=D.DEMO_LATEST_DATE, window_days=0)


def test_a_raised_threshold_filters_the_45_percent_case_out(suite):
    """告警线是可配置的：调到 50% 后，45% 的案例就不再进清单（口径可调，数字不改）。"""
    anomalies = suite.trigger.wow_anomalies(
        stat_date=D.DEMO_LATEST_DATE, threshold=0.50, include_new_hotspots=False
    )
    assert (D.DEMO_GRID_HOTSPOT, D.DEMO_TRIGGER_TYPE_AEB) not in {
        (a.geo_grid_id, a.trigger_type) for a in anomalies
    }
    assert all(a.wow_rate is not None and a.wow_rate >= 0.50 for a in anomalies)
    # 默认告警线下它是在的——证明被滤掉的原因就是那条线，而不是数据没了
    default_line = suite.trigger.wow_anomalies(
        stat_date=D.DEMO_LATEST_DATE, include_new_hotspots=False
    )
    assert (D.DEMO_GRID_HOTSPOT, D.DEMO_TRIGGER_TYPE_AEB) in {
        (a.geo_grid_id, a.trigger_type) for a in default_line
    }


# --------------------------------------------------------------------------- 落表：表 9


def test_trigger_heatmap_materialize_plan_uses_the_shared_geo_definitions():
    """★落 ads_trigger_heatmap★：批作业的网格键与热力等级都来自 ads.geo，不另写一份。"""
    sql = M.render_flink_sql("ads_trigger_heatmap")
    assert "INSERT INTO" in sql and "ads_trigger_heatmap" in sql
    # 网格键表达式逐字来自 geo.grid_id_sql
    assert geo.grid_id_sql("t.gps_lat", "t.gps_lon") in sql
    # 热力等级 CASE 逐字来自 geo.heat_level_sql
    assert geo.heat_level_sql("g.trigger_cnt") in sql
    # 脏 GPS 在批作业侧也要挡住，与 geo.grid_id 的越界校验同一意图
    assert "t.gps_lat IS NOT NULL AND t.gps_lon IS NOT NULL" in sql
    # 分组键 = 表的主键四元组
    for key in P.get_product("ads_trigger_heatmap").key_columns:
        assert key in sql


def test_trigger_heatmap_primary_key_is_the_four_dimension_tuple():
    """表 9 的计算维度：统计日期 × 车型 × 触发类型 × 地理网格。

    ⚠️ catalog 用 project_code 顶替了原文的「车型」段，这一处偏离登记在
    产品矩阵的 notes 里——测试把它钉住，免得哪天偏离被悄悄忘掉。
    """
    product = P.get_product("ads_trigger_heatmap")
    assert product.key_columns == ("stat_date", "project_code", "geo_grid_id", "trigger_type")
    assert product.dimensions_cn == "统计日期 × 车型 × 触发类型 × 地理网格"
    assert "⚠️ 原文维度含「车型」" in product.notes
    assert product.core_metrics_cn == ("触发总量", "上传/处理完成率", "入数据集量")


# ===========================================================================
# 五、★难例采纳率★（判据 A + C + D）
# ===========================================================================


def test_adoption_rate_is_adopted_over_mined_and_equals_87_point_5_percent():
    """★公式★：采纳率 = 训练采纳数 / 难例总数 = 2,800 / 3,200 = 87.5%。

    分母是**挖出的难例总数**，不是 Badcase 数、也不是关联 clip 数——
    原文原话「v3.2 评测挖出 3,200 条难例…训练平台采纳 2,800 条（采纳率 87.5%）」。
    """
    assert SV.adoption_rate(2_800, 3_200) == 0.875
    assert (
        SV.adoption_rate(C.HARD_CASE_DEMO_ADOPTED_COUNT, C.HARD_CASE_DEMO_TOTAL_COUNT)
        == C.HARD_CASE_DEMO_ADOPTION_RATE
    )


def test_adoption_rate_edge_cases():
    """没挖出难例就无所谓采纳率；负数是上游聚合错了，不能当 0 糊过去。"""
    assert SV.adoption_rate(0, 0) is None
    assert SV.adoption_rate(None, 3_200) is None
    assert SV.adoption_rate(2_800, None) is None
    assert SV.adoption_rate(0, 3_200) == 0.0
    with pytest.raises(ValueError, match="不能为负"):
        SV.adoption_rate(-1, 3_200)
    with pytest.raises(ValueError, match="不能为负"):
        SV.adoption_rate(2_800, -1)


def test_hard_case_summary_reproduces_the_source_case(suite):
    """★端到端★：三档难例合计 3,200、采纳合计 2,800，整体采纳率正好 87.5%。"""
    summary = suite.trigger.hard_case_adoption_summary(stat_date=D.DEMO_PUBLISH_DATE)
    assert summary.hard_case_count == C.HARD_CASE_DEMO_TOTAL_COUNT == 3_200
    assert summary.adopted_count == C.HARD_CASE_DEMO_ADOPTED_COUNT == 2_800
    assert summary.adoption_rate == 0.875

    by_category = {row.hard_case_category: row for row in summary.rows}
    assert by_category["夜间行人"].hard_case_count == C.HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT == 900
    assert by_category["逆光车辆"].hard_case_count == C.HARD_CASE_DEMO_BACKLIT_VEHICLE_COUNT == 700
    # 原文只给了两档，其余归「其他」，三档必须刚好补满 3,200
    assert sum(r.hard_case_count for r in summary.rows) == 3_200
    assert by_category["其他"].hard_case_count == 3_200 - 900 - 700 == 1_600


def test_hard_case_closed_loop_effect_is_quantified(suite):
    """「混入 v3.3 训练集，重训后夜间行人漏检率下降 60%」——闭环验证效果要落到行上。"""
    summary = suite.trigger.hard_case_adoption_summary(stat_date=D.DEMO_PUBLISH_DATE)
    night = next(r for r in summary.rows if r.hard_case_category == "夜间行人")
    assert night.model_version == C.BADCASE_DEMO_MODEL_VERSION == "v3.2"
    assert night.retrain_model_version == C.HARD_CASE_DEMO_RETRAIN_MODEL_VERSION == "v3.3"
    assert night.miss_rate_drop_pp == pytest.approx(C.HARD_CASE_DEMO_MISS_RATE_DROP_RATIO * 100.0)
    assert night.verified is True
    assert summary.verified_count == len(summary.rows)


def test_service_recomputes_the_adoption_rate_instead_of_trusting_the_column(suite):
    """服务层不信任列里的值：自己算一遍，对不上就在看板上标出来。"""
    rows = list(D.demo_rows()["ads_hard_case_library"])
    rows[0] = {**rows[0], "adoption_rate": 0.99}  # 物化口径漂了
    source = D.DemoRowSource({"ads_hard_case_library": rows})
    drifted = SV.ClosedLoopServiceSuite(Q.AdsQueryService(source))
    summary = drifted.trigger.hard_case_adoption_summary(stat_date=D.DEMO_PUBLISH_DATE)

    assert summary.adoption_rate == 0.875, "汇总仍按 2,800/3,200 算，不受脏列影响"
    assert len(summary.inconsistent_rows) == 1
    assert summary.inconsistent_rows[0].stored_adoption_rate == 0.99
    assert summary.inconsistent_rows[0].adoption_rate == pytest.approx(810 / 900)
    assert summary.as_dict()["inconsistent_row_count"] == 1


def test_adoption_rate_can_be_split_by_hard_case_category(suite):
    summary = suite.trigger.hard_case_adoption_summary(stat_date=D.DEMO_PUBLISH_DATE)
    by_cat = summary.by_category
    assert set(by_cat) == {"夜间行人", "逆光车辆", "其他"}
    assert by_cat["夜间行人"] == pytest.approx(810 / 900)
    assert by_cat["逆光车辆"] == pytest.approx(630 / 700)
    # 各档采纳数加总必须等于原文的 2,800
    assert sum(r.adopted_count for r in summary.rows) == 2_800


def test_hard_case_library_primary_key_matches_the_source_dimensions():
    """表 5 的计算维度：难例类别 × 来源 × 模型版本。"""
    product = P.get_product("ads_hard_case_library")
    assert product.dimensions_cn == "难例类别 × 来源 × 模型版本"
    assert product.key_columns == (
        "stat_date",
        "hard_case_category",
        "source_type",
        "model_version",
    )
    assert product.core_metrics_cn == ("数量", "采纳率", "闭环验证效果")


def test_hard_case_materialize_plan_computes_the_same_ratio():
    """★落 ads_hard_case_library★：批作业里的采纳率与 :func:`adoption_rate` 同式。"""
    sql = M.render_flink_sql("ads_hard_case_library")
    plan = M.PLANS["ads_hard_case_library"]
    assert plan.expressions["adoption_rate"] == (
        "CASE WHEN h.hard_case_count > 0 "
        "THEN CAST(a.adopted_count AS DOUBLE) / h.hard_case_count "
        "ELSE CAST(NULL AS DOUBLE) END"
    )
    # 分母为 0 时给 NULL，不给 0——与 Python 侧返回 None 同义
    assert "ELSE CAST(NULL AS DOUBLE) END" in sql
    # 三个来源：评测 / 回传 / 挖掘，对应 catalog 的 source_type 取值域
    for source_type in ("'evaluation'", "'trigger'", "'mining'"):
        assert source_type in sql


def test_closed_loop_status_links_triggers_to_hard_cases(suite):
    """代表 API ``trigger/.../closed-loop``：触发量 → 沉淀难例 → 采纳，一次问清。

    不传 ``stat_date`` 时每张表各取自己已物化的最新一天——热力图与难例库的
    T+1 进度未必同步，硬把一张表的日期套到另一张上会取出一片空。
    """
    status = suite.trigger.closed_loop_status(trigger_type=D.DEMO_TRIGGER_TYPE_AEB)
    assert status["trigger_type"] == D.DEMO_TRIGGER_TYPE_AEB
    assert status["stat_date"] == D.DEMO_LATEST_DATE
    assert status["trigger_cnt"] > 0
    assert status["grid_cnt"] == 2  # 热点网格 + 郊区网格
    assert status["adoption_rate"] == 0.875
    assert status["adopted_count"] == 2_800
    assert status["verified_count"] == 3

    # 指定日期时两张表用同一天：难例库只落在发布日那一天
    same_day = suite.trigger.closed_loop_status(
        trigger_type=D.DEMO_TRIGGER_TYPE_AEB, stat_date=D.DEMO_PUBLISH_DATE
    )
    assert same_day["stat_date"] == D.DEMO_PUBLISH_DATE
    assert same_day["adoption_rate"] == 0.875
    assert same_day["trigger_cnt"] > 0


def test_closed_loop_status_on_an_empty_lake_returns_zeros_not_a_crash(empty_suite):
    status = empty_suite.trigger.closed_loop_status(trigger_type="AEB误触发")
    assert status["trigger_cnt"] == 0
    assert status["grid_cnt"] == 0
    assert status["adoption_rate"] is None


# ===========================================================================
# 六、11 张表的服务化出口 · 零 JOIN · 六项闭环业务服务（判据 B + C）
# ===========================================================================


def test_every_one_of_the_eleven_tables_has_a_service_exit():
    """★开箱即用的底线★：11 张表每一张都至少被一条业务接口覆盖。"""
    assert SV.verify_service_exits() == {}
    covered = {table for binding in SV.API_BINDINGS for table in binding.tables}
    missing = [p.table for p in P.PRODUCTS if p.table not in covered]
    assert missing == [], f"这些表查得到但没出口：{missing}"
    assert len(covered) == 11


def test_every_api_binding_points_at_a_real_callable(suite):
    for binding in SV.API_BINDINGS:
        handler = suite.handler(binding)
        assert callable(handler), f"{binding.path} → {binding.attr}.{binding.method} 不可调用"
        assert binding.summary_cn
        assert binding.service in set(P.ClosedLoopService)


def test_api_bindings_only_read_tables_inside_the_matrix():
    for binding in SV.API_BINDINGS:
        for table in binding.tables:
            assert P.get_product(table).table == table


def test_the_six_closed_loop_services_match_the_source_table_row_by_row():
    """[S1-全景] 九：六项闭环业务服务、它们回答的问题、代表 API——逐字对账。"""
    expected = {
        P.ClosedLoopService.PRODUCTION_TRACKING: (
            "🚦 数据生产追踪",
            "这批数据到哪一步了？哪个环节最慢？",
            ("production/batch/.../progress",),
        ),
        P.ClosedLoopService.SCENE_SEARCH_CURATION: (
            "🎯 场景检索与样本圈选",
            "缺雨天数据，多久能从库里圈出来？",
            ("scene/search", "scene/curate"),
        ),
        P.ClosedLoopService.DATASET_VERSION_DELIVERY: (
            "📦 数据集版本与交付",
            "V3 的数据到底从哪来？谁用了它？",
            ("dataset/.../composition",),
        ),
        P.ClosedLoopService.MODEL_ITERATION_EVALUATION: (
            "🔁 模型迭代评测",
            "效果回退是数据问题还是模型问题？",
            ("model/compare", "badcase/root-cause"),
        ),
        P.ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP: (
            "🔄 回传与挖掘闭环",
            "触发到进训练集多久？缺口补上了吗？",
            ("trigger/.../closed-loop", "scene-gap/status"),
        ),
        P.ClosedLoopService.LINEAGE_TRACE: (
            "🧬 全链路血缘追溯",
            "Badcase 数据从哪来？问题数据影响了哪些模型？",
            ("lineage/business/trace", "lineage/impact"),
        ),
    }
    assert len(expected) == C.CLOSED_LOOP_SERVICE_COUNT == 6
    for service, (name, question, apis) in expected.items():
        assert service.name_cn == name
        assert service.question_cn == question
        assert service.representative_apis == apis


def test_every_representative_api_is_either_implemented_or_delegated():
    """原文列的代表 API，要么真落地，要么写明去处——不许假装覆盖。"""
    bound = {b.origin_api for b in SV.API_BINDINGS if b.origin_api}
    for service in P.ClosedLoopService:
        for api in service.representative_apis:
            assert api in bound or api in SV.DELEGATED_APIS, f"{api} 既没实现也没登记去处"
    # 唯一被委派出去的那条，去处写得够具体：粒度对不上，给了等价接口
    assert set(SV.DELEGATED_APIS) == {"production/batch/.../progress"}
    note = SV.DELEGATED_APIS["production/batch/.../progress"]
    assert "dwd_data_production_chain" in note
    assert "production/project/{project_code}/progress" in note


def test_the_suite_exposes_exactly_six_services(suite):
    assert set(suite.services) == set(P.ClosedLoopService)
    assert len(suite.services) == 6
    for service, impl in suite.services.items():
        assert impl.SERVICE is service


def test_each_service_reads_only_the_tables_the_matrix_assigned_to_it(suite):
    """越权护栏：一项服务只许读它在产品矩阵里登记的表。"""
    for service, impl in suite.services.items():
        assert set(impl.tables) == {p.table for p in P.products_by_service(service)}

    # 🔁 模型迭代评测想读热力图（表 9）就要越权——它必须走注入的 🔄 服务
    with pytest.raises(AdsQueryError, match="不读"):
        suite.model._latest("ads_trigger_heatmap")
    assert suite.model.trigger_service is suite.trigger
    assert "ads_trigger_heatmap" in suite.trigger.tables


def test_lineage_service_owns_no_ads_table_but_declares_its_anchor_tables(suite):
    """血缘事实在图库与 DWD 明细里，ADS 只提供追溯锚点。"""
    assert suite.lineage.tables == ()
    assert suite.lineage.ANCHOR_TABLES == (
        "ads_production_bottleneck_analysis",
        "ads_trigger_heatmap",
    )
    anchors = suite.lineage.trace_anchors(stat_date=D.DEMO_LATEST_DATE)
    assert anchors
    assert all(a["data_id"] for a in anchors)
    assert {a["source_table"] for a in anchors} <= set(suite.lineage.ANCHOR_TABLES)


def test_lineage_and_semantic_search_fail_loudly_without_their_subsystems(suite):
    """不由本层实现的两项能力显式抛错并给指引，而不是静默降级。"""
    with pytest.raises(ServiceUnavailableError, match="lineage_port"):
        suite.lineage.trace(data_id="COLLECT_BP_20260301123045_b7e2")
    with pytest.raises(ServiceUnavailableError, match="semantic_port"):
        suite.scene.semantic_search("雨天夜间高速行人横穿")
    with pytest.raises(ServiceUnavailableError, match="向量"):
        R.route_for(R.WorkloadKind.SEMANTIC_RETRIEVAL)


def test_lineage_traversal_depth_is_capped_at_the_source_range(suite):
    """[S1-全景] 八②：多跳遍历建议限定 3-5 跳防止扇出爆炸。"""
    assert (C.LINEAGE_MIN_TRAVERSAL_DEPTH, C.LINEAGE_MAX_TRAVERSAL_DEPTH) == (3, 5)
    assert C.LINEAGE_QUERY_DIRECTIONS == (
        "forward_trace",
        "backward_trace",
        "version_branch_compare",
        "impact_analysis",
    )
    for depth in (2, 6):
        with pytest.raises(AdsQueryError, match="跳数"):
            suite.lineage.trace(data_id="X", depth=depth)
    with pytest.raises(AdsQueryError, match="方向"):
        suite.lineage.trace(data_id="X", direction="sideways")


# --------------------------------------------------------------------------- 零 JOIN


def test_no_ads_query_ever_renders_a_join():
    """★零 JOIN★：[S1-05] 一「ADS 存答案」的前提就是查询侧不再拼表。

    结构性证明：每张表渲染出来的 SQL 只有一个 FROM、一张表，出现任何 JOIN 关键字
    都说明出口破防了。
    """
    for table in P.ADS_TABLE_NAMES:
        sql, _ = Q.AdsQuery(table).render()
        upper = sql.upper()
        assert upper.count("FROM") == 1
        assert " JOIN " not in upper
        assert "UNION" not in upper
        assert sql.count(f"`{table}`") == 1


def test_cross_table_conclusions_are_assembled_in_memory_not_pushed_down(suite):
    """需要两张表的结论（项目级进度 = 大盘 + 瓶颈）在内存里合，不下推成跨表 JOIN。"""
    progress = suite.production.progress(
        project_code=D.DEMO_PROJECT_CODE, stat_date=D.DEMO_PUBLISH_DATE
    )
    assert progress["overview"] is not None
    assert len(progress["stages"]) == len(C.PRODUCTION_STAGE_NAMES) == 4
    assert progress["bottleneck_stage"] == "质检"
    # 原文场景①：其中 120 条在质检环节停留超 48 小时
    assert progress["blocked_over_48h_count"] == C.SCENARIO_BLOCKED_CLIP_COUNT == 120
    assert progress["blocked_threshold_hours"] == C.PRODUCTION_BLOCKED_ALERT_HOURS == 48


def test_service_exit_matrix_lists_every_table_with_its_paths():
    rows = SV.service_exit_matrix()
    assert len(rows) == 11
    for row in rows:
        assert row["服务化出口"], f"{row['表名']} 的服务化出口是空的"
        assert row["闭环业务服务"]


# --------------------------------------------------------------------------- 其余四张表的出口


def test_scene_library_gap_list_reproduces_the_construction_zone_case(suite):
    """表 4 案例：「施工区域」仅 150 条（达标线 2,000 条）→ 进缺口清单；已达标的不进。"""
    gaps = suite.scene.gap_list(stat_date=D.DEMO_PUBLISH_DATE)
    assert [r["tag_name"] for r in gaps] == ["施工区域"]
    row = gaps[0]
    assert row["total_data_count"] == C.SCENE_GAP_DEMO_CURRENT_COUNT == 150
    assert row["target_count"] == C.SCENE_GAP_DEMO_TARGET_COUNT == 2_000
    assert row["gap_count"] == 2_000 - 150 == 1_850
    assert row["coverage_status"] == "GAP"

    status = suite.scene.search(coverage_status="COVERED", stat_date=D.DEMO_PUBLISH_DATE)
    assert [r["tag_name"] for r in status] == ["夜间行人"]
    with pytest.raises(AdsQueryError, match="覆盖状态"):
        suite.scene.search(coverage_status="DONE")


def test_scene_gap_status_counts_the_three_states(suite):
    """代表 API ``scene-gap/status``：GAP → FILLING → COVERED 三态分布。"""
    status = suite.trigger.scene_gap_status(stat_date=D.DEMO_PUBLISH_DATE)
    assert status["status_flow"] == ["GAP", "FILLING", "COVERED"]
    assert status["counts"] == {"GAP": 1, "FILLING": 0, "COVERED": 1}
    assert status["tag_count"] == 2
    assert status["gap_total"] == 1_850


def test_asset_catalog_reproduces_the_core_and_idle_assets(suite):
    """表 6 案例：核心资产被 12 个训练任务引用、评分 4.6；3 个低分老旧资产建议归档。"""
    assets = suite.dataset.asset_catalog(stat_date=D.DEMO_PUBLISH_DATE)
    core = assets[0]
    assert core["asset_name"] == C.ASSET_DEMO_NAME
    assert core["ref_count_90d"] == C.ASSET_DEMO_REF_COUNT_90D == 12
    assert core["quality_score"] == C.ASSET_DEMO_QUALITY_SCORE == 4.6

    candidates = suite.dataset.archive_candidates(stat_date=D.DEMO_PUBLISH_DATE)
    assert len(candidates) == C.ASSET_DEMO_IDLE_ASSET_COUNT == 3
    assert all(r["ref_count_90d"] == 0 for r in candidates)
    assert all(r["quality_score"] < C.ASSET_LOW_QUALITY_SCORE_THRESHOLD for r in candidates)
    assert core["asset_id"] not in {r["asset_id"] for r in candidates}
    assert suite.dataset.idle_days_threshold == C.ASSET_IDLE_DAYS_THRESHOLD == 90


def test_badcase_root_cause_distribution_reproduces_the_45_and_28_percent(suite):
    """表 3 案例：3,200 个 Badcase，感知漏检 45%，其中夜间行人 28%。"""
    rows = suite.model.badcase_root_cause(
        model_version=C.BADCASE_DEMO_MODEL_VERSION, stat_date=D.DEMO_PUBLISH_DATE
    )
    assert rows
    total = sum(r["badcase_count"] for r in rows)
    assert total == C.BADCASE_DEMO_TOTAL_COUNT == 3_200
    perception = sum(r["badcase_count"] for r in rows if r["root_cause_category"] == "感知漏检")
    assert (
        perception / total
        == pytest.approx(C.BADCASE_DEMO_PERCEPTION_MISS_RATIO)
        == (pytest.approx(0.45))
    )
    night = next(r for r in rows if r["root_cause_sub_category"] == "夜间行人")
    assert (
        night["badcase_count"] / total
        == pytest.approx(C.BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO)
        == pytest.approx(0.28)
    )
    # 原文说夜间行人「趋势上升」；catalog 的 trend 列取值域是 up/down/flat
    assert night["trend"] == "up"
    assert all(r["trend"] in ("up", "down", "flat") for r in rows)


def test_model_compare_reproduces_the_pass_rates_and_the_regression(suite):
    """表 7 案例：总体 91.2% vs 87.5%；夜间 +8.3pp；高速 −1.2pp 被标成回归项。"""
    rows = suite.model.compare(
        model_version="v3.3", baseline_model_version="v3.2", dataset_id="EVAL-NOA-CITY"
    )
    by_scene = {r["scene_type"]: r for r in rows}
    assert by_scene["ALL"]["pass_rate"] == 0.912
    assert by_scene["ALL"]["baseline_pass_rate"] == 0.875
    assert by_scene["ALL"]["pass_rate_diff_pp"] == pytest.approx(3.7)
    assert by_scene["夜间"]["pass_rate_diff_pp"] == 8.3
    assert by_scene["高速"]["pass_rate_diff_pp"] == -1.2
    assert by_scene["高速"]["regression_flag"] is True
    assert by_scene["夜间"]["regression_flag"] is False


def test_storage_cost_dashboard_reproduces_the_94_percent_saving(suite):
    """表 10 案例：240GB 数据年成本 ¥3,226 → ¥203，降幅约 94%；环比超 10% 告警。"""
    assert C.STORAGE_COST_DEMO_VOLUME_GB == 240
    assert C.STORAGE_COST_DEMO_UNGOVERNED_YEARLY_YUAN == 3_226
    assert C.STORAGE_COST_DEMO_GOVERNED_YEARLY_YUAN == 203
    assert C.STORAGE_COST_DEMO_SAVING_RATIO == 0.94
    assert C.STORAGE_COST_MOM_ALERT_THRESHOLD == 0.10
    rows = suite.dataset.storage_cost(stat_date=D.DEMO_PUBLISH_DATE)
    assert rows
    alerts = suite.dataset.cost_alerts(stat_date=D.DEMO_PUBLISH_DATE)
    assert all(
        (r.get("cost_mom_rate") or 0.0) > C.STORAGE_COST_MOM_ALERT_THRESHOLD
        or r.get("budget_alert_flag") is True
        for r in alerts
    )


def test_mining_tag_dashboard_reports_the_three_sources(suite):
    """表 11：三来源（采集/规则/模型）标签量构成 + clip 覆盖率健康线。"""
    assert C.MINING_TAG_SOURCES == ("collect", "rule", "vlm")
    board = suite.trigger.mining_tag_dashboard(stat_date=D.DEMO_PUBLISH_DATE)
    assert set(board["by_source"]) == set(C.MINING_TAG_SOURCES)
    assert board["source_names"] == list(C.MINING_TAG_SOURCES)
    assert board["coverage_warn_threshold"] == C.MINING_TAG_COVERAGE_WARN_THRESHOLD == 0.60
    assert board["pending_review_warn_count"] == C.MINING_TAG_PENDING_REVIEW_WARN_COUNT == 500
    assert sum(board["by_source"].values()) > 0


# ===========================================================================
# 七、查询层与选路（判据 C）
# ===========================================================================


def test_dashboard_workloads_are_materialized_and_adhoc_goes_to_paimon():
    """[S1-全景] 七选路表：大屏走内表（毫秒级、不受 Compaction 影响），即席查走外部表。"""
    dashboard = R.route_for(R.WorkloadKind.DASHBOARD_REPORT)
    assert dashboard.route is R.QueryRoute.STARROCKS_INTERNAL
    assert dashboard.reason_cn == "物化后稳定可控，不受湖端 Compaction 影响"
    adhoc = R.route_for(R.WorkloadKind.EXPLORATORY_ADHOC)
    assert adhoc.route is R.QueryRoute.PAIMON_EXTERNAL
    assert adhoc.reason_cn == "零搬运零冗余，永远查最新数据"
    # 内表 `db`.`table`，外部表 `catalog`.`db`.`table`
    assert R.qualify("ads_trigger_heatmap", R.QueryRoute.STARROCKS_INTERNAL).count(".") == 1
    assert R.qualify("ads_trigger_heatmap", R.QueryRoute.PAIMON_EXTERNAL).count(".") == 2


def test_identifiers_never_come_from_caller_input():
    """字段白名单同时是 SQL 注入防线——标识符只从白名单取。"""
    with pytest.raises(UnknownColumnError):
        Q.AdsQuery("ads_trigger_heatmap", columns=("trigger_cnt; DROP TABLE x",))
    with pytest.raises(UnknownColumnError):
        Q.Filter("geo_grid_id__", "=", "x").render("ads_trigger_heatmap")
    with pytest.raises(Q.InvalidFilterError, match="不支持的算子"):
        Q.Filter("geo_grid_id", "; DROP TABLE x; --", "1")
    # 取值一律走 %s 占位符
    sql, params = Q.AdsQuery(
        "ads_trigger_heatmap", filters=(Q.Filter("geo_grid_id", "=", "'; DROP TABLE x; --"),)
    ).render()
    assert "DROP TABLE" not in sql
    assert params == ["'; DROP TABLE x; --"]


def test_query_limits_are_bounded_on_both_ends():
    assert C.ADS_QUERY_DEFAULT_LIMIT == 500
    assert C.ADS_QUERY_MAX_LIMIT == 10_000
    for bad in (0, -1, C.ADS_QUERY_MAX_LIMIT + 1):
        with pytest.raises(AdsQueryError, match="limit"):
            Q.AdsQuery("ads_trigger_heatmap", limit=bad)
    with pytest.raises(AdsQueryError, match="offset"):
        Q.AdsQuery("ads_trigger_heatmap", offset=-1)


def test_static_row_source_refuses_a_column_the_table_does_not_have():
    """内存行源与真表必须同构：多一列在测试里查得到、在 StarRocks 上是 Unknown column。"""
    with pytest.raises(UnknownColumnError, match="表上没有的列"):
        Q.StaticRowSource({"ads_trigger_heatmap": [{"stat_date": "2026-09-03", "made_up": 1}]})
    # 少给列是允许的（缺的当 NULL）
    Q.StaticRowSource({"ads_trigger_heatmap": [{"stat_date": "2026-09-03"}]})


def test_every_demo_row_is_structurally_a_row_of_its_table():
    """演示数据本身也要过同构这道关，否则「产品评审能看、上线看不到」。"""
    for table, rows in D.demo_rows().items():
        allowed = set(S.column_names(table))
        for index, row in enumerate(rows):
            extra = set(row) - allowed
            assert extra == set(), f"{table} 第 {index} 行多了列：{sorted(extra)}"


def test_invalidate_cache_by_table_actually_clears_that_table():
    """★缓存失效回归★：按表清缓存必须真的清掉，否则 T+1 跑完大屏还在吃旧数据。

    曾经的缓存键第一段是从 SQL 文本里反解的（``sql.split("`")[-2]``），
    带 ORDER BY 时取到的是排序列、带 WHERE 时取到的是过滤列，
    于是 ``invalidate_cache(table)`` 永远匹配不到、返回 0，而且**不报错**。
    """
    svc = Q.AdsQueryService(D.DemoRowSource())
    query = Q.AdsQuery(
        "ads_trigger_heatmap",
        filters=(Q.Filter("stat_date", "=", D.DEMO_LATEST_DATE),),
        order_by=(("trigger_cnt", "DESC"),),
    )
    assert svc.fetch(query).from_cache is False
    assert svc.fetch(query).from_cache is True

    assert svc.invalidate_cache("ads_trigger_heatmap") == 1
    assert svc.fetch(query).from_cache is False

    # 清别的表不该误伤这张表
    svc.fetch(query)
    assert svc.invalidate_cache("ads_hard_case_library") == 0
    assert svc.fetch(query).from_cache is True

    with pytest.raises(UnknownTableError):
        svc.invalidate_cache("ads_not_a_table")


def test_latest_snapshot_returns_nothing_when_nothing_is_materialized(empty_suite):
    """T+1 还没跑：返回空列表，而不是把「没有数据」渲染成 0。"""
    assert empty_suite.trigger.heatmap() == []
    assert empty_suite.production.closed_loop_overview() == []
    assert empty_suite.ads.latest_stat_date("ads_trigger_heatmap") is None
    # 没有日期维度的表（表 8 按 ota_task_id 汇总）本来就没有「最新一天」
    assert empty_suite.ads.latest_stat_date("ads_ota_deployment_summary") is None
    assert P.get_product("ads_ota_deployment_summary").date_column is None


def test_aggregation_paging_refuses_to_truncate(monkeypatch):
    """聚合窗口超上限时报错而不是截断——少算的总量会直接污染 OTA 放行结论。"""
    monkeypatch.setattr(SV, "ADS_AGGREGATION_MAX_ROWS", 10)
    monkeypatch.setattr(SV, "ADS_QUERY_MAX_LIMIT", 5)
    rows = [
        {
            "stat_date": date(2026, 9, 3),
            "project_code": "P",
            "geo_grid_id": f"31.{n:02d}_121.47",
            "trigger_type": "AEB误触发",
            "trigger_cnt": 1,
        }
        for n in range(30)
    ]
    svc = SV.ClosedLoopServiceSuite(
        Q.AdsQueryService(Q.StaticRowSource({"ads_trigger_heatmap": rows}))
    )
    with pytest.raises(AdsQueryError, match="上限"):
        svc.trigger.trigger_volume(start=date(2026, 9, 3), end=date(2026, 9, 3))


def test_describe_tells_a_platform_what_a_table_answers():
    """业务平台接入时先调 describe——「这张表能回答什么问题」不用翻文档。"""
    info = Q.AdsQueryService.describe("ads_ota_deployment_summary")
    assert info["ordinal"] == 8
    assert info["title_cn"] == "OTA 部署汇总表"
    assert info["refresh"] == C.ADS_REFRESH_MODE == "T+1"
    assert info["core_metrics"] == [
        "升级任务数/车辆数",
        "成功率",
        "灰度进度",
        "发布后回传触发量",
    ]
    assert "deploy_success_rate" in info["columns"]
    assert info["key_columns"] == ["ota_task_id"]


# ===========================================================================
# 八、统一 API 网关（判据 B + C）
# ===========================================================================


def _gateway(suite: SV.ClosedLoopServiceSuite) -> G.ApiGateway:
    gw = G.ApiGateway()
    suite.register_routes(gw)
    gw.authenticator.register(
        "tok-dash",
        G.Principal(G.BusinessPlatform.MONITOR_DASHBOARD, "monitor-dashboard"),
    )
    return gw


def test_all_business_apis_register_on_the_gateway(suite):
    gw = G.ApiGateway()
    assert suite.register_routes(gw) == len(SV.API_BINDINGS)
    assert {r["path"] for r in gw.routes()} == {b.path for b in SV.API_BINDINGS}
    # 同名路径重复注册会被拒绝，避免静默覆盖
    with pytest.raises(ValueError, match="重复注册"):
        suite.register_routes(gw)


def test_release_gate_is_reachable_through_the_gateway(suite):
    """★端到端★：业务平台 → 网关（认证/限流/审计）→ 🔁 服务 → 三条件放行。"""
    gw = _gateway(suite)
    result = gw.handle(
        f"ota/{D.DEMO_OTA_TASK_ID}/release-gate",
        token="tok-dash",
        params={"safety_issue_count": 0, "check_regression": False},
    )
    assert result.decision == "full_rollout"
    assert result.grey_passed is True

    audit = gw.audit.recent(1)[0]
    assert audit["path"] == "ota/{ota_task_id}/release-gate"
    assert audit["ok"] is True
    assert audit["subject"] == "monitor-dashboard"
    assert "token" not in audit["params"], "令牌永远不进审计"


def test_gateway_records_a_failed_call_too(suite):
    gw = _gateway(suite)
    with pytest.raises(AdsQueryError):
        gw.handle("ota/NOPE/release-gate", token="tok-dash", params={"safety_issue_count": 0})
    audit = gw.audit.recent(1)[0]
    assert audit["ok"] is False
    assert "AdsQueryError" in audit["error"]


def test_scopes_confine_a_platform_to_its_own_apis(suite):
    gw = _gateway(suite)
    gw.authenticator.register(
        "tok-mining",
        G.Principal(G.BusinessPlatform.DATA_MINING, "mining", scopes=frozenset({"trigger/"})),
    )
    cells = gw.handle(
        "trigger/heatmap", token="tok-mining", params={"stat_date": D.DEMO_LATEST_DATE}
    )
    assert len(cells) > 0 and all(isinstance(c, SV.HeatCell) for c in cells)
    with pytest.raises(AuthorizationError):
        gw.handle("storage/cost", token="tok-mining")


def test_gateway_error_status_maps_every_family(suite):
    """传输无关：网关只抛异常，状态码由接入方按这张表贴。"""
    from adas_lakehouse.ads.errors import (
        AuthenticationError,
        RateLimitExceededError,
        RouteNotFoundError,
        StarRocksUnavailableError,
    )

    assert G.error_status(AuthenticationError("x")) == 401
    assert G.error_status(AuthorizationError("x")) == 403
    assert G.error_status(RateLimitExceededError("x")) == 429
    assert G.error_status(RouteNotFoundError("x")) == 404
    assert G.error_status(StarRocksUnavailableError("x")) == 503
    assert G.error_status(AdsQueryError("x")) == 400
    assert G.error_status(RuntimeError("x")) == 500
    assert G.is_gateway_error(AuthorizationError("x")) is True
    assert G.is_gateway_error(AdsQueryError("x")) is False


def test_gateway_is_transport_agnostic_by_design():
    """ads/gateway.py 刻意不绑 HTTP 框架——这是设计决策，不是缺陷，钉死它。"""
    source = inspect.getsource(G)
    for framework in ("flask", "fastapi", "starlette", "django", "aiohttp", "tornado"):
        assert f"import {framework}" not in source.lower()
    assert "传输无关" in (G.__doc__ or "")


# ===========================================================================
# 九、物化与 catalog 缺列登记（判据 B + C）
# ===========================================================================


def test_every_ads_table_has_a_materialize_plan_and_renders():
    """11 张表各一个 Flink 批作业 + 一条内表物化语句 = 22 个作业。"""
    assert set(M.PLANS) == set(P.ADS_TABLE_NAMES)
    rendered = M.render_all_flink_sql()
    assert len(rendered) == 11
    for filename, sql in rendered.items():
        table = filename.removesuffix(".sql")
        assert "INSERT INTO" in sql
        assert table in sql
        # 批模式 + sink 到 Paimon 主键表即 UPSERT，重跑同一天幂等覆盖
        assert "SET 'execution.runtime-mode' = 'batch';" in sql
    jobs = M.AdsMaterializer().plan("2026-08-28")
    assert len(jobs) == 22
    assert [name for name, _ in jobs[:11]] == [f"flink_{t}" for t in P.ADS_TABLE_NAMES]
    assert [name for name, _ in jobs[11:]] == [f"starrocks_{t}" for t in P.ADS_TABLE_NAMES]
    # 调度变量被绑定，不该有残留占位符
    assert all("${stat_date}" not in sql for _, sql in jobs)
    assert all("${month_start}" not in sql for _, sql in jobs)


def test_materializer_without_a_submitter_refuses_to_pretend_it_ran():
    with pytest.raises(ServiceUnavailableError, match="SqlSubmitter"):
        M.AdsMaterializer().run("2026-08-28")


def test_internal_tables_are_upsert_and_idempotent_on_rerun():
    """T+1 重刷必须幂等：内表用主键模型 + INSERT OVERWRITE，不会一天两行。"""
    ddl = S.render_all_starrocks_ddl()
    assert ddl.upper().count("CREATE TABLE") == 11
    assert ddl.upper().count("PRIMARY KEY") >= 11
    statements = [line for line in ddl.splitlines() if line.startswith("INSERT OVERWRITE")]
    assert len(statements) == 11
    # 不能是 INSERT INTO：补数/重刷会把同一天的指标翻倍
    assert "INSERT INTO `adas_ads`" not in ddl


def test_registered_catalog_gaps_are_still_real_gaps():
    """★缺列登记要保鲜★：登记过的缺列如果 catalog 已经补上，这条登记就该删。

    反过来更重要：登记一条「缺列」却其实存在，等于给自己发了张免死金牌。
    """
    assert set(SV.CATALOG_GAPS) <= set(P.ADS_TABLE_NAMES)
    columns = {t: set(S.column_names(t)) for t in SV.CATALOG_GAPS}

    # 表 8 确实没有「发布后回传触发量」与「安全相关问题数」
    ota = columns["ads_ota_deployment_summary"]
    assert not any("trigger" in c for c in ota)
    assert not any("safety" in c for c in ota)
    # 表 9 确实没有「上传/处理完成率」与「入数据集量」
    heatmap = columns["ads_trigger_heatmap"]
    assert not any(c.startswith("upload_") or c.startswith("process_") for c in heatmap)
    assert "into_dataset_cnt" not in heatmap
    assert not any("wow" in c or "mom" in c for c in heatmap)
    # 表 7 确实没有数据集名称列（其余 ADS 表都为零 JOIN 冗余了展示名）
    assert "dataset_name" not in columns["ads_model_version_comparison"]
    assert "asset_name" in S.column_names("ads_data_asset_catalog")
    assert "project_name" in S.column_names("ads_ota_deployment_summary")


def test_catalog_gap_notes_point_at_the_source_metric_they_are_missing():
    ota_notes = " ".join(SV.CATALOG_GAPS["ads_ota_deployment_summary"])
    assert "发布后回传触发量" in ota_notes
    assert "安全相关问题 0 起" in ota_notes
    heatmap_notes = " ".join(SV.CATALOG_GAPS["ads_trigger_heatmap"])
    assert "上传/处理完成率" in heatmap_notes and "入数据集量" in heatmap_notes
    compare_notes = " ".join(SV.CATALOG_GAPS["ads_model_version_comparison"])
    assert C.MODEL_COMPARE_DEMO_DATASET_NAME in compare_notes


def test_the_self_check_cli_is_green_and_exits_zero(capsys):
    """``python -m adas_lakehouse.ads.materialize`` 是 CI 可直接当断言用的自检入口。"""
    assert M._main([]) == 0
    out = capsys.readouterr().out
    assert "产品矩阵与 catalog 注册表一致" in out
    assert "张 ADS 表全部有服务化出口" in out
    for table in P.ADS_TABLE_NAMES:
        assert table in out


@pytest.mark.parametrize("module", [C, P, S, R, Q, geo, SV, G, M, D])
def test_docstring_examples_actually_run(module):
    """docstring 里的 ``>>>`` 是对外承诺，必须真能跑。

    项目的 ``testpaths`` 只收 ``tests/``，doctest 不会被自动执行——
    于是 ``evaluate_ota_release_gate`` 那几个「99.2% 放行 / 1 起安全问题不放行」的
    示例就成了没人验的文档。这里把它们拉进来跑一遍。
    """
    import doctest

    result = doctest.testmod(module, verbose=False)
    assert result.failed == 0, f"{module.__name__} 的 docstring 示例跑挂了"


def test_the_ota_gate_docstring_pins_the_three_numbers():
    """光「能跑」不够：示例本身要覆盖 99.2% 放行、99.19% 不放行、1 起安全问题不放行。"""
    doc = inspect.getdoc(SV.evaluate_ota_release_gate) or ""
    assert doc.count(">>>") >= 3
    assert "deploy_success_rate=0.992," in doc
    assert "deploy_success_rate=0.9919," in doc
    assert "safety_issue_count=1)" in doc
    assert "(True, 'full_rollout')" in doc
    assert "(False, ('deploy_success_rate',))" in doc
    assert "(False, ('safety_issue_count',))" in doc


# ===========================================================================
# 十、包门面（判据 B）
# ===========================================================================


def test_the_package_reexports_its_public_api():
    """ADS 与其余子系统一样，必须有一个包级门面——否则调用方只能按模块路径摸。"""
    import adas_lakehouse.ads as ads

    assert ads.__all__
    missing = [name for name in ads.__all__ if not hasattr(ads, name)]
    assert missing == [], f"__all__ 里列了但没导出：{missing}"
    # 四条主线能力都要能从包顶层直接拿到
    for name in (
        "evaluate_ota_release_gate",
        "adoption_rate",
        "heat_level",
        "grid_id",
        "ClosedLoopServiceSuite",
        "verify_service_exits",
        "PRODUCTS",
    ):
        assert name in ads.__all__


def test_public_helpers_are_exported_not_stranded():
    """孤岛检查：既然没有内部调用方，至少要在 __all__ 里，并说明是给谁用的。"""
    assert "ensure_columns" in Q.__all__
    assert "供外部编排调用的公开 API" in (Q.ensure_columns.__doc__ or "")
    assert "is_gateway_error" in G.__all__ and "error_status" in G.__all__
    assert "供外部传输层调用的公开 API" in (G.is_gateway_error.__doc__ or "")
    assert "service_exit_matrix" in SV.__all__

    assert Q.ensure_columns("ads_trigger_heatmap", ("trigger_cnt", "heat_level")) == (
        "trigger_cnt",
        "heat_level",
    )
    with pytest.raises(UnknownColumnError, match="缺少字段"):
        Q.ensure_columns("ads_trigger_heatmap", ("trigger_cnt", "nope_1", "nope_2"))
