"""演示行源：把原文的业务案例数字灌成 11 张 ADS 表的行。

:mod:`constants` 的 docstring 里写明了那些案例数字（3,200 个 Badcase、87.5% 采纳率、
99.2% 升级成功率……）的两个用途，其一就是「作为演示数据源（DemoRowSource）」——
本模块即那个行源。它让服务层在**不连 StarRocks** 的前提下端到端跑起来：
产品评审的假数据大屏、验收阶段的接口自检、以及对着原文数字的回归测试。

三条纪律：

  1. **一个数字都不新编**：能落到行上的值一律引用 :mod:`constants`，
     常量里没有的（比如某一天的分日触发量）由本模块按原文给的**周合计**倒推分摊，
     分摊算法写在 :func:`_spread` 里，合计严格等于原文数字；
  2. **口径与服务层同源**：热力等级用 :func:`geo.heat_level` 现算，
     采纳率用 :func:`services.adoption_rate` 现算，不手填；
  3. **不是生产数据**：这些行是「演示数据取自业务场景举例」（[S1-05] 各图注原话），
     不能当成真实统计值，也不参与任何物化作业。

原文两处读法歧义，本模块的选择都写在下面，不藏着：

  · 表 3「感知漏检占 45%，其中夜间行人占 28%」——「28%」的分母原文没明说
    （可能是全部 Badcase，也可能是感知漏检那一档）。本模块按**占全部 Badcase 28%**
    落行：夜间行人 896 条 = 3,200 × 28%，感知漏检合计 1,440 条 = 3,200 × 45%。
  · 表 5「重训后夜间行人漏检率下降 60%」是**相对降幅**，而 catalog 的列
    ``miss_rate_drop_pp`` 单位是**百分点**。本模块按原文数字逐字落 60.0，
    单位口径的冲突登记在 :data:`services.CATALOG_GAPS`，由收口阶段统一处理。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final

from .constants import (
    ASSET_DEMO_IDLE_ASSET_COUNT,
    ASSET_DEMO_NAME,
    ASSET_DEMO_QUALITY_SCORE,
    ASSET_DEMO_REF_COUNT_90D,
    ASSET_LOW_QUALITY_SCORE_THRESHOLD,
    BADCASE_DEMO_MODEL_VERSION,
    BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO,
    BADCASE_DEMO_PERCEPTION_MISS_RATIO,
    BADCASE_DEMO_TARGETED_COLLECT_COUNT,
    BADCASE_DEMO_TOTAL_COUNT,
    CLOSED_LOOP_BASELINE_HOURS,
    CLOSED_LOOP_TARGET_HOURS,
    HARD_CASE_DEMO_ADOPTED_COUNT,
    HARD_CASE_DEMO_BACKLIT_VEHICLE_COUNT,
    HARD_CASE_DEMO_MISS_RATE_DROP_RATIO,
    HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT,
    HARD_CASE_DEMO_RETRAIN_MODEL_VERSION,
    HARD_CASE_DEMO_TOTAL_COUNT,
    MINING_TAG_COVERAGE_WARN_THRESHOLD,
    MODEL_COMPARE_DEMO_BASELINE_PASS_RATE,
    MODEL_COMPARE_DEMO_BASELINE_VERSION,
    MODEL_COMPARE_DEMO_HIGHWAY_REGRESSION_PP,
    MODEL_COMPARE_DEMO_MODEL_VERSION,
    MODEL_COMPARE_DEMO_NIGHT_GAIN_PP,
    MODEL_COMPARE_DEMO_PASS_RATE,
    OTA_DEMO_GREY_VEHICLE_COUNT,
    OTA_DEMO_SUCCESS_RATE,
    OTA_DEMO_TRIGGER_GROWTH_RATE,
    OTA_POST_RELEASE_OBSERVE_DAYS,
    PRODUCTION_STAGE_NAMES,
    SCENARIO_BATCH_CLIP_COUNT,
    SCENARIO_BLOCKED_CLIP_COUNT,
    SCENE_GAP_DEMO_CURRENT_COUNT,
    SCENE_GAP_DEMO_TARGET_COUNT,
    SCENE_LIBRARY_COVERAGE_RATE,
    STORAGE_COST_DEMO_GOVERNED_YEARLY_YUAN,
    STORAGE_COST_DEMO_SAVING_RATIO,
    STORAGE_COST_DEMO_UNGOVERNED_YEARLY_YUAN,
    STORAGE_COST_DEMO_VOLUME_GB,
    STORAGE_COST_MOM_ALERT_THRESHOLD,
    TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE,
    TRIGGER_HEATMAP_DEMO_HOTSPOT,
    TRIGGER_WOW_WINDOW_DAYS,
)
from .geo import grid_id, heat_level
from .query import Row, StaticRowSource
from .services import adoption_rate

__all__ = [
    "DEMO_PUBLISH_DATE",
    "DEMO_LATEST_DATE",
    "DEMO_OTA_TASK_ID",
    "DEMO_PROJECT_CODE",
    "DEMO_GRID_HOTSPOT",
    "DEMO_GRID_SUBURB",
    "DEMO_TRIGGER_TYPE_AEB",
    "DEMO_TRIGGER_TYPE_TAKEOVER",
    "DEMO_PREV_WEEK_TRIGGER_TOTAL",
    "DEMO_CURRENT_WEEK_TRIGGER_TOTAL",
    "demo_rows",
    "DemoRowSource",
]

#: 发布日 = 原文发表日（[S1-05] 2026-08-28），也是灰度推送那一天
DEMO_PUBLISH_DATE: Final[date] = date(2026, 8, 28)
#: 发布后一周的最后一天，也是各表「最新已物化的一天」
DEMO_LATEST_DATE: Final[date] = DEMO_PUBLISH_DATE + timedelta(
    days=OTA_POST_RELEASE_OBSERVE_DAYS - 1
)
DEMO_PROJECT_CODE: Final[str] = "NOA-CITY"
DEMO_OTA_TASK_ID: Final[str] = f"OTA-{MODEL_COMPARE_DEMO_MODEL_VERSION}-GREY"
DEMO_SOFTWARE_VERSION: Final[str] = f"SW-{MODEL_COMPARE_DEMO_MODEL_VERSION}"

#: 「城区晚高峰路口」那个网格（[S1-05] 表 9「触发集中在城区晚高峰路口」）
DEMO_GRID_HOTSPOT: Final[str] = grid_id(31.2345, 121.4678)
#: 对照组网格：同城郊区，涨幅温和，不该进异常清单
DEMO_GRID_SUBURB: Final[str] = grid_id(31.3012, 121.5034)
DEMO_TRIGGER_TYPE_AEB: Final[str] = "AEB误触发"
DEMO_TRIGGER_TYPE_TAKEOVER: Final[str] = "接管"

# 周合计（本模块按原文倒推的分摊基数，合计严格等于原文口径）：
#   · AEB 误触发在热点网格：上周 200 次 → 本周 290 次，正好 +45%
#     （[S1-05] 表 9「某区域「AEB 误触发」周环比上升 45%」）
#   · 全量：上周 1,000 次 → 本周 1,300 次，正好 +30%
#     （[S1-05] 表 8「发布后一周回传触发量环比增长 30%」）
DEMO_PREV_WEEK_AEB_HOTSPOT: Final[int] = 200
DEMO_CURRENT_WEEK_AEB_HOTSPOT: Final[int] = int(
    round(DEMO_PREV_WEEK_AEB_HOTSPOT * (1.0 + TRIGGER_HEATMAP_DEMO_AEB_WOW_RISE))
)
DEMO_PREV_WEEK_TRIGGER_TOTAL: Final[int] = 1_000
DEMO_CURRENT_WEEK_TRIGGER_TOTAL: Final[int] = int(
    round(DEMO_PREV_WEEK_TRIGGER_TOTAL * (1.0 + OTA_DEMO_TRIGGER_GROWTH_RATE))
)
#: 郊区网格的接管触发：上周 100 → 本周 110（+10%），低于告警线，用来验证「不误报」
DEMO_PREV_WEEK_SUBURB_TAKEOVER: Final[int] = 100
DEMO_CURRENT_WEEK_SUBURB_TAKEOVER: Final[int] = 110
#: 郊区网格的 AEB：上周 0 → 本周 20，用来验证「新增热点」这条边界分支
DEMO_CURRENT_WEEK_SUBURB_AEB: Final[int] = 20


def _spread(total: int, days: int) -> list[int]:
    """把一个周合计平摊到每天，余数落在最后一天——合计严格守恒。

    Raises:
        ValueError: 天数非正，或合计为负。
    """
    if days <= 0:
        raise ValueError(f"天数必须为正，收到 {days}")
    if total < 0:
        raise ValueError(f"合计不能为负，收到 {total}")
    base, remainder = divmod(total, days)
    out = [base] * days
    out[-1] += remainder
    return out


def _week(end: date, days: int = TRIGGER_WOW_WINDOW_DAYS) -> list[date]:
    """以 ``end`` 结尾的 ``days`` 天窗口（升序）。"""
    return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _heatmap_rows() -> list[Row]:
    """表 9：两周 × 两个网格 × 两种触发类型的热力图行。

    热力等级用 :func:`geo.heat_level` 现算，与 Flink 批作业同一口径。
    """
    current_days = _week(DEMO_LATEST_DATE)
    previous_days = _week(current_days[0] - timedelta(days=1))

    hotspot_takeover_prev = (
        DEMO_PREV_WEEK_TRIGGER_TOTAL - DEMO_PREV_WEEK_AEB_HOTSPOT - DEMO_PREV_WEEK_SUBURB_TAKEOVER
    )
    hotspot_takeover_current = (
        DEMO_CURRENT_WEEK_TRIGGER_TOTAL
        - DEMO_CURRENT_WEEK_AEB_HOTSPOT
        - DEMO_CURRENT_WEEK_SUBURB_TAKEOVER
        - DEMO_CURRENT_WEEK_SUBURB_AEB
    )

    series: list[tuple[str, str, list[date], int, str, str]] = [
        (
            DEMO_GRID_HOTSPOT,
            DEMO_TRIGGER_TYPE_AEB,
            previous_days,
            DEMO_PREV_WEEK_AEB_HOTSPOT,
            "urban_intersection",
            TRIGGER_HEATMAP_DEMO_HOTSPOT,
        ),
        (
            DEMO_GRID_HOTSPOT,
            DEMO_TRIGGER_TYPE_AEB,
            current_days,
            DEMO_CURRENT_WEEK_AEB_HOTSPOT,
            "urban_intersection",
            TRIGGER_HEATMAP_DEMO_HOTSPOT,
        ),
        (
            DEMO_GRID_HOTSPOT,
            DEMO_TRIGGER_TYPE_TAKEOVER,
            previous_days,
            hotspot_takeover_prev,
            "urban_intersection",
            TRIGGER_HEATMAP_DEMO_HOTSPOT,
        ),
        (
            DEMO_GRID_HOTSPOT,
            DEMO_TRIGGER_TYPE_TAKEOVER,
            current_days,
            hotspot_takeover_current,
            "urban_intersection",
            TRIGGER_HEATMAP_DEMO_HOTSPOT,
        ),
        (
            DEMO_GRID_SUBURB,
            DEMO_TRIGGER_TYPE_TAKEOVER,
            previous_days,
            DEMO_PREV_WEEK_SUBURB_TAKEOVER,
            "suburb_arterial",
            "郊区主干道",
        ),
        (
            DEMO_GRID_SUBURB,
            DEMO_TRIGGER_TYPE_TAKEOVER,
            current_days,
            DEMO_CURRENT_WEEK_SUBURB_TAKEOVER,
            "suburb_arterial",
            "郊区主干道",
        ),
        # 上周为 0：郊区 AEB 只在本周出现，用来覆盖「新增热点」分支
        (
            DEMO_GRID_SUBURB,
            DEMO_TRIGGER_TYPE_AEB,
            current_days,
            DEMO_CURRENT_WEEK_SUBURB_AEB,
            "suburb_arterial",
            "郊区主干道",
        ),
    ]

    rows: list[Row] = []
    for grid, trigger_type, days, total, road_type, scene_tag in series:
        lat, lon = (float(part) for part in grid.split("_"))
        for day, count in zip(days, _spread(total, len(days)), strict=True):
            rows.append(
                {
                    "stat_date": day,
                    "project_code": DEMO_PROJECT_CODE,
                    "geo_grid_id": grid,
                    "trigger_type": trigger_type,
                    "grid_center_lat": lat,
                    "grid_center_lon": lon,
                    "city_code": "310000",
                    "city_name": "上海市",
                    "road_type": road_type,
                    "trigger_cnt": count,
                    "heat_level": heat_level(count),
                    "vehicle_cnt": max(1, count // 3),
                    "hard_case_cnt": count // 5,
                    "top_scene_tag": scene_tag,
                    "sample_data_id": f"COLLECT_BP_{day:%Y%m%d}120000_{grid[-4:]}",
                }
            )
    return rows


def _hard_case_rows() -> list[Row]:
    """表 5：v3.2 挖出 3,200 条难例、采纳 2,800 条（采纳率 87.5%）。

    夜间行人 900 / 逆光车辆 700 是原文给的两档，其余归「其他」——三档难例数合计
    等于原文的 3,200，三档采纳数合计等于原文的 2,800，整体采纳率因此正好 87.5%。
    """
    night = HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT
    backlit = HARD_CASE_DEMO_BACKLIT_VEHICLE_COUNT
    others = HARD_CASE_DEMO_TOTAL_COUNT - night - backlit
    adopted_night = 810
    adopted_backlit = 630
    adopted_others = HARD_CASE_DEMO_ADOPTED_COUNT - adopted_night - adopted_backlit

    def row(category: str, count: int, adopted: int, drop_pp: float | None) -> Row:
        return {
            "stat_date": DEMO_PUBLISH_DATE,
            "hard_case_category": category,
            "source_type": "evaluation",
            "model_version": BADCASE_DEMO_MODEL_VERSION,
            "project_code": DEMO_PROJECT_CODE,
            "hard_case_count": count,
            "adopted_count": adopted,
            "adoption_rate": adoption_rate(adopted, count),
            "adopted_dataset_id": "DS-NOA-CITY",
            "adopted_dataset_version": "v13",
            "retrain_model_version": HARD_CASE_DEMO_RETRAIN_MODEL_VERSION,
            "metric_gain_pp": MODEL_COMPARE_DEMO_NIGHT_GAIN_PP if drop_pp else None,
            # ⚠️ 原文「漏检率下降 60%」是相对降幅，本列单位是百分点——见模块 docstring
            "miss_rate_drop_pp": drop_pp,
            "badcase_count": count,
            "related_data_count": count,
            "avg_difficulty_score": 0.82,
            "pending_review_count": 0,
            "closed_loop_status": "verified",
        }

    return [
        row("夜间行人", night, adopted_night, HARD_CASE_DEMO_MISS_RATE_DROP_RATIO * 100.0),
        row("逆光车辆", backlit, adopted_backlit, None),
        row("其他", others, adopted_others, None),
    ]


def _badcase_rows() -> list[Row]:
    """表 3：v3.2 评测产出 3,200 个 Badcase，感知漏检 45%，其中夜间行人 28%。"""
    total = BADCASE_DEMO_TOTAL_COUNT
    night = int(round(total * BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO))
    perception = int(round(total * BADCASE_DEMO_PERCEPTION_MISS_RATIO))
    perception_others = perception - night
    rest = total - perception

    def row(category: str, sub: str, count: int, rank: int, trend: str) -> Row:
        return {
            "stat_date": DEMO_PUBLISH_DATE,
            "project_code": DEMO_PROJECT_CODE,
            "model_version": BADCASE_DEMO_MODEL_VERSION,
            "root_cause_category": category,
            "root_cause_sub_category": sub,
            "evaluation_type": "regression",
            "badcase_count": count,
            "badcase_ratio": count / total,
            "rank_no": rank,
            "trend": trend,
            "wow_change_rate": 0.12 if trend == "up" else -0.03,
            "mom_change_rate": None,
            "severity_p0_count": 0,
            "related_scene_tag": sub,
            "related_data_count": count,
            "suggested_action": "定向补采+重训" if trend == "up" else "重训",
            "suggested_collect_count": (
                BADCASE_DEMO_TARGETED_COLLECT_COUNT if trend == "up" else 0
            ),
            "owner_team": None,
        }

    return [
        row("感知漏检", "夜间行人", night, 1, "up"),
        row("感知漏检", "其他", perception_others, 2, "flat"),
        row("其他根因", "其他", rest, 3, "flat"),
    ]


def _model_compare_rows() -> list[Row]:
    """表 7：v3.3 vs v3.2 在「城区 NOA 评测集 v5」上的对比（91.2% vs 87.5%）。"""
    dataset_id = "EVAL-NOA-CITY"

    def row(scene: str, pass_rate: float, baseline: float, diff_pp: float) -> Row:
        return {
            "model_version": MODEL_COMPARE_DEMO_MODEL_VERSION,
            "baseline_model_version": MODEL_COMPARE_DEMO_BASELINE_VERSION,
            "dataset_id": dataset_id,
            "dataset_version": "v5",
            "scene_type": scene,
            "project_code": DEMO_PROJECT_CODE,
            "model_type": "e2e",
            "evaluation_type": "regression",
            "eval_case_cnt": 1_000,
            "pass_rate": pass_rate,
            "baseline_pass_rate": baseline,
            "pass_rate_diff_pp": diff_pp,
            "badcase_cnt": int(round(1_000 * (1.0 - pass_rate))),
            "badcase_rate": 1.0 - pass_rate,
            "baseline_badcase_rate": 1.0 - baseline,
            "avg_metric_score": pass_rate,
            "baseline_avg_metric_score": baseline,
            "regression_flag": diff_pp < 0,
            "conclusion": "回归" if diff_pp < 0 else "显著提升",
            "stat_date": DEMO_PUBLISH_DATE,
            # ⚠️ 原文案例的数据集是按名字引用的（「城区 NOA 评测集 v5」＝
            #    MODEL_COMPARE_DEMO_DATASET_NAME），但 ads_model_version_comparison
            #    没有 dataset_name 列，只能落 dataset_id + dataset_version。
            #    缺列已登记在 services.CATALOG_GAPS，这里不硬塞一个表上没有的列——
            #    塞了只会让内存行源与真表分叉。
        }

    overall_diff_pp = round(
        (MODEL_COMPARE_DEMO_PASS_RATE - MODEL_COMPARE_DEMO_BASELINE_PASS_RATE) * 100.0, 1
    )
    night_baseline = 0.860
    highway_baseline = 0.905
    return [
        row(
            "ALL",
            MODEL_COMPARE_DEMO_PASS_RATE,
            MODEL_COMPARE_DEMO_BASELINE_PASS_RATE,
            overall_diff_pp,
        ),
        row(
            "夜间",
            night_baseline + MODEL_COMPARE_DEMO_NIGHT_GAIN_PP / 100.0,
            night_baseline,
            MODEL_COMPARE_DEMO_NIGHT_GAIN_PP,
        ),
        row(
            "高速",
            highway_baseline + MODEL_COMPARE_DEMO_HIGHWAY_REGRESSION_PP / 100.0,
            highway_baseline,
            MODEL_COMPARE_DEMO_HIGHWAY_REGRESSION_PP,
        ),
    ]


def _ota_rows() -> list[Row]:
    """表 8：v3.3 灰度推送 500 台车，升级成功率 99.2%。

    失败车辆数由成功率倒推（500 × 99.2% = 496 成功、4 失败），
    ``deploy_success_rate`` 因此正好落在原文的 99.2%。
    """
    target = OTA_DEMO_GREY_VEHICLE_COUNT
    deployed = int(round(target * OTA_DEMO_SUCCESS_RATE))
    failed = target - deployed
    return [
        {
            "ota_task_id": DEMO_OTA_TASK_ID,
            "task_name": f"{MODEL_COMPARE_DEMO_MODEL_VERSION} 灰度推送",
            "project_code": DEMO_PROJECT_CODE,
            "project_name": "城区 NOA",
            "software_version": DEMO_SOFTWARE_VERSION,
            "model_version": MODEL_COMPARE_DEMO_MODEL_VERSION,
            "release_channel": "grey",
            "publish_time": DEMO_PUBLISH_DATE,
            "target_vehicle_count": target,
            "deployed_vehicle_count": deployed,
            "fail_vehicle_count": failed,
            "rollback_vehicle_count": 0,
            "deploy_progress_rate": 1.0,
            "deploy_success_rate": deployed / target,
            "avg_deploy_duration_sec": 1_800.0,
            "top_fail_reason": "下载超时",
            "vehicle_model_distribution": '{"models":"M1,M2"}',
            "deploy_status": "finished",
        }
    ]


def _dashboard_rows() -> list[Row]:
    """表 1：闭环大盘——优化前 216 小时 → 优化后 96 小时（[S1-05] 第六章）。"""

    def row(day: date, hours: int) -> Row:
        return {
            "stat_date": day,
            "project_code": DEMO_PROJECT_CODE,
            "project_name": "城区 NOA",
            "total_data_count": SCENARIO_BATCH_CLIP_COUNT * 10,
            "month_new_data_count": SCENARIO_BATCH_CLIP_COUNT,
            "total_capacity_tb": 1_800.0,
            "avg_closed_loop_hours": float(hours),
            "collect_to_delivery_hours": float(hours) / 2.0,
            "training_duration_hours": float(hours) / 4.0,
            "evaluation_duration_hours": float(hours) / 4.0,
            "collecting_data_count": 200,
            "producing_data_count": 400,
            "delivered_data_count": SCENARIO_BATCH_CLIP_COUNT,
            "trained_data_count": SCENARIO_BATCH_CLIP_COUNT,
            "badcase_total_count": BADCASE_DEMO_TOTAL_COUNT,
            "badcase_resolve_rate": 0.86,
            "data_growth_rate": 0.08,
            "efficiency_improve_rate": 1.0 - CLOSED_LOOP_TARGET_HOURS / CLOSED_LOOP_BASELINE_HOURS,
            "health_score": 0.82,
            "bottleneck_stage": PRODUCTION_STAGE_NAMES[-1],
        }

    return [
        row(DEMO_PUBLISH_DATE - timedelta(days=7), CLOSED_LOOP_BASELINE_HOURS),
        row(DEMO_PUBLISH_DATE, CLOSED_LOOP_TARGET_HOURS),
    ]


def _bottleneck_rows() -> list[Row]:
    """表 2：四段产线环节，质检环节积压 120 条超 48 小时（[S1-全景] 第五章场景①）。"""
    durations = (6.0, 8.0, 24.0, 40.0)
    rows: list[Row] = []
    ranked = sorted(range(len(durations)), key=lambda i: -durations[i])
    for index, (stage, hours) in enumerate(zip(PRODUCTION_STAGE_NAMES, durations, strict=True)):
        rows.append(
            {
                "stat_date": DEMO_PUBLISH_DATE,
                "project_code": DEMO_PROJECT_CODE,
                "stage_code": f"S{index + 1}",
                "stage_name": stage,
                "stage_order": index + 1,
                "avg_duration_hour": hours,
                "duration_ratio": hours / sum(durations),
                "backlog_data_count": 0 if stage != PRODUCTION_STAGE_NAMES[-1] else 300,
                "blocked_over_48h_count": (
                    SCENARIO_BLOCKED_CLIP_COUNT if stage == PRODUCTION_STAGE_NAMES[-1] else 0
                ),
                "affected_batch_count": 1,
                "throughput_per_hour": 80.0,
                "capacity_utilization": 0.7,
                "bottleneck_rank": ranked.index(index) + 1,
                "bottleneck_level": "P1" if stage == PRODUCTION_STAGE_NAMES[-1] else "P3",
                "root_cause": "人力不足" if stage == PRODUCTION_STAGE_NAMES[-1] else None,
                "suggestion": None,
                "trend_vs_prev_day": 0.05,
                "sample_data_id": f"COLLECT_BP_{DEMO_PUBLISH_DATE:%Y%m%d}093000_s{index + 1}",
            }
        )
    return rows


def _scene_rows() -> list[Row]:
    """表 4：「施工区域」仅 150 条（达标线 2,000 条）→ GAP；另一条已 COVERED。"""

    def row(tag: str, total: int, target: int, status: str) -> Row:
        return {
            "stat_date": DEMO_PUBLISH_DATE,
            "scene_type": "城区",
            "scene_tag_id": f"TAG-{tag}",
            "tag_code": tag,
            "tag_name": tag,
            "priority": "P0" if status == "GAP" else "P2",
            "covered_project_count": 1,
            "total_data_count": total,
            "high_quality_count": int(total * 0.8),
            "high_quality_rate": 0.8,
            "related_badcase_count": 120,
            "badcase_mom_rate": 0.15 if status == "GAP" else -0.02,
            "target_count": target,
            "coverage_rate": min(1.0, total / target),
            "gap_count": max(0, target - total),
            "coverage_status": status,
            "dataset_ref_count": 3,
            "mining_task_count": 1 if status == "FILLING" else 0,
            "trend_7d": "rising" if status == "GAP" else "flat",
            "last_supplement_time": None,
        }

    return [
        row("施工区域", SCENE_GAP_DEMO_CURRENT_COUNT, SCENE_GAP_DEMO_TARGET_COUNT, "GAP"),
        row("夜间行人", SCENE_GAP_DEMO_TARGET_COUNT, SCENE_GAP_DEMO_TARGET_COUNT, "COVERED"),
    ]


def _asset_rows() -> list[Row]:
    """表 6：核心资产被 12 个训练任务引用、质量评分 4.6；另有 3 个低分闲置资产。"""
    rows: list[Row] = [
        {
            "stat_date": DEMO_PUBLISH_DATE,
            "asset_type": "dataset",
            "asset_id": "DS-NOA-CITY@v12",
            "asset_name": ASSET_DEMO_NAME,
            "dataset_id": "DS-NOA-CITY",
            "dataset_version": "v12",
            "project_code": DEMO_PROJECT_CODE,
            "business_domain": "perception",
            "owner": "alice",
            "owner_dept": "感知算法",
            "data_count": 120_000,
            "storage_size_bytes": 240 * 1024**3,
            "storage_tier": "warm",
            "ref_count": ASSET_DEMO_REF_COUNT_90D,
            "ref_count_90d": ASSET_DEMO_REF_COUNT_90D,
            "last_used_time": None,
            "quality_score": ASSET_DEMO_QUALITY_SCORE,
            "asset_status": "active",
            "archive_suggestion": "keep",
            "register_time": None,
        }
    ]
    for index in range(ASSET_DEMO_IDLE_ASSET_COUNT):
        rows.append(
            {
                "stat_date": DEMO_PUBLISH_DATE,
                "asset_type": "dataset",
                "asset_id": f"DS-LEGACY-{index + 1}@v1",
                "asset_name": f"老旧数据集 {index + 1}",
                "dataset_id": f"DS-LEGACY-{index + 1}",
                "dataset_version": "v1",
                "project_code": DEMO_PROJECT_CODE,
                "business_domain": "perception",
                "owner": "bob",
                "owner_dept": "感知算法",
                "data_count": 1_000,
                "storage_size_bytes": 10 * 1024**3,
                "storage_tier": "cold",
                "ref_count": 0,
                "ref_count_90d": 0,
                "last_used_time": None,
                # 低于低分线，配合零引用构成归档建议
                "quality_score": ASSET_LOW_QUALITY_SCORE_THRESHOLD - 0.5,
                "asset_status": "idle",
                "archive_suggestion": "archive",
                "register_time": None,
            }
        )
    return rows


def _storage_rows() -> list[Row]:
    """表 10：240GB 雨夜城区数据的治理账——年成本 ¥3,226 → ¥203，降幅约 94%。"""
    volume_tb = STORAGE_COST_DEMO_VOLUME_GB / 1024.0
    baseline_month = STORAGE_COST_DEMO_UNGOVERNED_YEARLY_YUAN / 12.0
    governed_month = STORAGE_COST_DEMO_GOVERNED_YEARLY_YUAN / 12.0
    return [
        {
            "stat_date": DEMO_PUBLISH_DATE,
            "storage_media": "oss_standard",
            "lifecycle_stage": "warm",
            "data_type": "collect_clip",
            "total_capacity_tb": volume_tb,
            "capacity_ratio": 1.0,
            "month_cost_yuan": governed_month,
            "unit_price_yuan_gb_month": governed_month / STORAGE_COST_DEMO_VOLUME_GB,
            "preheat_volume_tb": 0.0,
            "tier_down_volume_tb": volume_tb,
            "evict_volume_tb": 0.0,
            "delete_volume_tb": 0.0,
            "baseline_cost_yuan": baseline_month,
            "saved_cost_yuan": baseline_month - governed_month,
            "nas_peak_usage": 0.42,
            "preheat_hit_rate": 0.9,
            "archive_restore_count": 0,
            "cost_mom_rate": -STORAGE_COST_DEMO_SAVING_RATIO,
            "capacity_mom_rate": 0.03,
            "budget_alert_flag": False,
        },
        {
            # 一行越过成本告警线，用来验证告警接口真的会亮
            "stat_date": DEMO_PUBLISH_DATE,
            "storage_media": "nas",
            "lifecycle_stage": "hot",
            "data_type": "training_cache",
            "total_capacity_tb": volume_tb * 2,
            "capacity_ratio": 0.5,
            "month_cost_yuan": baseline_month,
            "unit_price_yuan_gb_month": baseline_month / STORAGE_COST_DEMO_VOLUME_GB,
            "preheat_volume_tb": volume_tb,
            "tier_down_volume_tb": 0.0,
            "evict_volume_tb": 0.0,
            "delete_volume_tb": 0.0,
            "baseline_cost_yuan": baseline_month,
            "saved_cost_yuan": 0.0,
            "nas_peak_usage": 0.85,
            "preheat_hit_rate": 0.7,
            "archive_restore_count": 1,
            "cost_mom_rate": STORAGE_COST_MOM_ALERT_THRESHOLD + 0.05,
            "capacity_mom_rate": 0.2,
            "budget_alert_flag": True,
        },
    ]


def _mining_tag_rows() -> list[Row]:
    """表 11：三来源（采集/规则/模型）标签量构成与 clip 覆盖率。"""
    return [
        {
            "stat_date": DEMO_PUBLISH_DATE,
            "project_code": DEMO_PROJECT_CODE,
            "tag_id": "TAG-施工区域",
            "tag_name": "施工区域",
            "tag_category": "场景",
            "parent_tag_id": None,
            "tag_level": 2,
            "tag_status": "active",
            "data_count": SCENE_GAP_DEMO_CURRENT_COUNT,
            "image_count": SCENE_GAP_DEMO_CURRENT_COUNT * 30,
            "data_ratio": 0.1,
            "image_ratio": 0.1,
            "collect_source_count": 50,
            "rule_source_count": 60,
            "vlm_source_count": 40,
            "avg_confidence": 0.78,
            # 低于覆盖健康线，验证看板会给出覆盖不足的提示
            "coverage_ratio": MINING_TAG_COVERAGE_WARN_THRESHOLD - 0.1,
            "gap_level": "high",
            "rank_in_category": 1,
            "dod_change_ratio": 0.05,
        },
        {
            "stat_date": DEMO_PUBLISH_DATE,
            "project_code": DEMO_PROJECT_CODE,
            "tag_id": "TAG-夜间行人",
            "tag_name": "夜间行人",
            "tag_category": "场景",
            "parent_tag_id": None,
            "tag_level": 2,
            "tag_status": "active",
            "data_count": HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT,
            "image_count": HARD_CASE_DEMO_NIGHT_PEDESTRIAN_COUNT * 30,
            "data_ratio": 0.3,
            "image_ratio": 0.3,
            "collect_source_count": 300,
            "rule_source_count": 400,
            "vlm_source_count": 200,
            "avg_confidence": 0.91,
            "coverage_ratio": SCENE_LIBRARY_COVERAGE_RATE,
            "gap_level": "low",
            "rank_in_category": 2,
            "dod_change_ratio": 0.02,
        },
    ]


def demo_rows() -> dict[str, list[Row]]:
    """11 张 ADS 表的演示行（键是表名，可直接喂给 :class:`query.StaticRowSource`）。"""
    return {
        "ads_closed_loop_dashboard": _dashboard_rows(),
        "ads_production_bottleneck_analysis": _bottleneck_rows(),
        "ads_badcase_root_cause_distribution": _badcase_rows(),
        "ads_scene_library_summary": _scene_rows(),
        "ads_hard_case_library": _hard_case_rows(),
        "ads_data_asset_catalog": _asset_rows(),
        "ads_model_version_comparison": _model_compare_rows(),
        "ads_ota_deployment_summary": _ota_rows(),
        "ads_trigger_heatmap": _heatmap_rows(),
        "ads_storage_cost_dashboard": _storage_rows(),
        "ads_mining_tag_dashboard": _mining_tag_rows(),
    }


class DemoRowSource(StaticRowSource):
    """装满原文案例数据的内存行源——不连 StarRocks 也能把六项服务跑通。

    Examples:
        >>> from adas_lakehouse.ads.query import AdsQueryService
        >>> from adas_lakehouse.ads.services import ClosedLoopServiceSuite
        >>> suite = ClosedLoopServiceSuite(AdsQueryService(DemoRowSource()))
        >>> decision = suite.model.ota_release_gate(
        ...     ota_task_id=DEMO_OTA_TASK_ID, safety_issue_count=0, check_regression=False)
        >>> decision.grey_passed
        True
    """

    def __init__(self, extra: dict[str, list[Row]] | None = None) -> None:
        tables: dict[str, Any] = demo_rows()
        tables.update(extra or {})
        super().__init__(tables)
