"""ADS 两段式 T+1 物化：DWS/DWD → Paimon ADS → StarRocks 内表。

[S1-05] 第一章：「StarRocks 离线加工 DWS/DWD 层数据，物化为 StarRocks 内表，
T+1 批加工、毫秒级直查」；[S1-全景] 第四章又把 ADS 列为湖仓四层的第四层（11 张表）。
本项目把两处口径都落地为两段（理由见 schema 模块 docstring）：

    ① Flink 批作业   DWS/DWD(Paimon) → ADS(Paimon)      口径固化在湖内，单一事实源
    ② StarRocks      ADS(Paimon 外部表) → ADS 内表       毫秒直查出口，大屏与固定报表走这里

本模块负责生成这两段的 SQL：
    flink/sql/ads_<表名>.sql   —— 第 ① 段，每张表一个批作业
    ddl/starrocks_ads.sql      —— 第 ② 段，建库 + 11 张内表 DDL + INSERT OVERWRITE

生成而不是手写的理由：ADS 的字段清单是 catalog 注册表里的 TableSpec（单一事实源），
手写 SQL 必然与湖表定义漂移。这里只手写「每个字段从上游哪来」这一份映射，
字段清单、类型、NULL 占位一律由代码按 TableSpec 渲染。

⚠️ 原文未公开任何一张 ADS 表的加工 SQL，本模块的上游映射全部是本项目按
[S1-全景] 第五章的「核心落表链路」+ catalog 里 DWS/DWD 表的实际字段推断的。
映射不到的字段渲染成 `CAST(NULL AS <type>)` 并在行尾标注，不臆造口径。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from ..catalog.spec import Column
from ..config import settings
from .constants import (
    ASSET_HOT_WINDOW_DAYS,
    ASSET_LOW_QUALITY_SCORE_THRESHOLD,
    BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO,
    BADCASE_DEMO_PERCEPTION_MISS_RATIO,
    BOTTLENECK_DURATION_RATIO_THRESHOLD,
    BOTTLENECK_MOM_DETERIORATION_THRESHOLD,
    CLOSED_LOOP_TARGET_HOURS,
    GEO_GRID_PRECISION_DEGREES,
    MODEL_REGRESSION_TOLERANCE_PP_DEFAULT,
    NAS_PEAK_USAGE_ALERT_THRESHOLD,
    PRODUCTION_BLOCKED_ALERT_HOURS,
    STORAGE_COST_MOM_ALERT_THRESHOLD,
    TRIGGER_HEAT_LEVEL_MAX,
    TRIGGER_HEAT_LEVEL_MIN,
    TRIGGER_HEAT_LEVEL_THRESHOLDS,
)
from .errors import ServiceUnavailableError
from .geo import grid_id_sql, heat_level_sql
from .products import PRODUCTS, AdsProduct, get_product
from .schema import load_table_spec, render_all_starrocks_ddl

__all__ = [
    "MaterializePlan",
    "PLANS",
    "render_flink_sql",
    "render_all_flink_sql",
    "write_sql_files",
    "SqlSubmitter",
    "ScriptExportSubmitter",
    "AdsMaterializer",
]

#: SQL 里引用湖表用 ``@表名`` 标记，渲染时替换成 `catalog`.`database`.`表` 的全限定名。
_TABLE_MARK: Final[str] = "@"

#: 表 9 的网格键表达式。与服务层 ``ads.geo.grid_id`` 同一份定义（见 geo 模块 docstring），
#: 批作业算出来的 geo_grid_id 与服务层重算出来的永远逐字相同。
_GEO_GRID_SQL: Final[str] = grid_id_sql("t.gps_lat", "t.gps_lon")


@dataclass(frozen=True, slots=True)
class MaterializePlan:
    """一张 ADS 表的第 ① 段（Flink 批作业）加工计划。

    Args:
        table: ADS 表名。
        from_clause: FROM 及其后的 JOIN 子句，湖表用 ``@表名`` 引用。
        expressions: {ADS 字段 -> SELECT 表达式}。没登记的字段渲染成 NULL 占位。
        where: WHERE 子句（不含 WHERE 关键字），空表示全量重算。
        inferred_notes: {ADS 字段 -> 该字段为何是 NULL / 口径如何推断} 的说明，
            渲染进 SQL 注释，保证「哪些是原文口径、哪些是本项目补的」一眼可辨。
    """

    table: str
    from_clause: str
    expressions: dict[str, str]
    where: str = ""
    inferred_notes: dict[str, str] = field(default_factory=dict)

    @property
    def product(self) -> AdsProduct:
        return get_product(self.table)


def _q(table: str) -> str:
    """湖表全限定名。"""
    cfg = settings().paimon
    return f"`{cfg.catalog}`.`{cfg.database}`.`{table}`"


def _expand(sql: str) -> str:
    """把 ``@表名`` 展开成全限定名。"""
    out: list[str] = []
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == _TABLE_MARK:
            j = i + 1
            while j < len(sql) and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append(_q(sql[i + 1 : j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ===========================================================================
# 11 张表的上游映射
# ===========================================================================
# 约定：
#   · 每个 plan 的 FROM 子查询里就把日期过滤做掉（分区/主键裁剪尽量早）
#   · ${stat_date} / ${month_start} 由调度器按 T+1 注入（yyyy-MM-dd）
#   · 阈值类数字一律引用 constants，SQL 里出现的每个数字都在注释里标了出处
# ===========================================================================

_PLAN_LIST: list[MaterializePlan] = [
    # ---------------------------------------------------------------- 表 1
    MaterializePlan(
        table="ads_closed_loop_dashboard",
        from_clause="""@dws_closed_loop_efficiency e
LEFT JOIN @dws_closed_loop_efficiency prev
       ON prev.project_code = e.project_code
      AND prev.stat_date = e.stat_date - INTERVAL '1' DAY
LEFT JOIN (
  -- 月度新增：本月 1 号到 T 日的供给量累加（[S1-05] 表 1「总数据量与月度新增」）
  SELECT project_code, SUM(clip_total_count) AS month_new_data_count
  FROM @dws_closed_loop_efficiency
  WHERE stat_date BETWEEN DATE '${month_start}' AND DATE '${stat_date}'
  GROUP BY project_code
) mtd ON mtd.project_code = e.project_code
LEFT JOIN (
  -- ⚠️ 存储成本 DWS 没有项目维度，容量只能取全湖口径
  SELECT stat_date, SUM(total_capacity_tb) AS total_capacity_tb
  FROM @dws_closed_loop_storage_cost_daily
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date
) st ON st.stat_date = e.stat_date""",
        where="e.stat_date = DATE '${stat_date}'",
        expressions={
            "stat_date": "e.stat_date",
            "project_code": "e.project_code",
            "total_data_count": "e.clip_total_count",
            "month_new_data_count": "mtd.month_new_data_count",
            "total_capacity_tb": "st.total_capacity_tb",
            "avg_closed_loop_hours": "e.closed_loop_duration_hours",
            "collect_to_delivery_hours": "e.collect_to_delivery_hours",
            "training_duration_hours": "e.training_duration_hours",
            "evaluation_duration_hours": "e.evaluation_duration_hours",
            "producing_data_count": "e.clip_total_count - e.delivered_clip_count",
            "delivered_data_count": "e.delivered_clip_count",
            "trained_data_count": "e.trained_clip_count",
            "badcase_total_count": "e.badcase_total_count",
            "badcase_resolve_rate": "e.badcase_resolve_rate",
            "data_growth_rate": (
                "CASE WHEN prev.clip_total_count > 0 "
                "THEN (e.clip_total_count - prev.clip_total_count) "
                "/ CAST(prev.clip_total_count AS DOUBLE) ELSE CAST(NULL AS DOUBLE) END"
            ),
            "efficiency_improve_rate": "e.efficiency_improve_rate",
            "health_score": (
                "CASE WHEN e.closed_loop_duration_hours > 0 THEN ROUND(\n"
                f"         0.4 * CASE WHEN {CLOSED_LOOP_TARGET_HOURS}.0 "
                "/ e.closed_loop_duration_hours > 1.0 THEN 1.0\n"
                f"                    ELSE {CLOSED_LOOP_TARGET_HOURS}.0 "
                "/ e.closed_loop_duration_hours END\n"
                "       + 0.3 * COALESCE(e.badcase_resolve_rate, 0.0)\n"
                "       + 0.3 * CASE WHEN e.clip_total_count > 0\n"
                "                    THEN CAST(e.delivered_clip_count AS DOUBLE) "
                "/ e.clip_total_count ELSE 0.0 END, 4)\n"
                "     ELSE CAST(NULL AS DOUBLE) END"
            ),
            "bottleneck_stage": "e.bottleneck_stage",
        },
        inferred_notes={
            "project_name": "项目名称维表（ods 项目维表）尚未在 catalog 登记，装配阶段补 LEFT JOIN",
            "total_capacity_tb": "全湖口径：dws_closed_loop_storage_cost_daily 无 project_code 维度",
            "collecting_data_count": "DWS 只给了总量/已交付/已训练三档，采集中数量需回 dwd_data_production_chain",
            "health_score": (
                f"⚠️ 原文未明确，本项目设计：0.4×闭环耗时达成度（目标 "
                f"{CLOSED_LOOP_TARGET_HOURS} 小时，[S1-05] 第六章）+ 0.3×Badcase 解决率 + 0.3×交付率"
            ),
        },
    ),
    # ---------------------------------------------------------------- 表 2
    MaterializePlan(
        table="ads_production_bottleneck_analysis",
        from_clause="""(
  SELECT p.*,
         prev.avg_duration_hour AS prev_avg_duration_hour,
         q.qc_pass_rate,
         SUM(p.avg_duration_hour) OVER (PARTITION BY p.stat_date, p.project_code)
           AS total_duration_hour,
         RANK() OVER (PARTITION BY p.stat_date, p.project_code
                      ORDER BY p.avg_duration_hour DESC) AS bottleneck_rank
  FROM @dws_production_efficiency_daily p
  LEFT JOIN @dws_production_efficiency_daily prev
         ON prev.project_code = p.project_code
        AND prev.stage_code = p.stage_code
        AND prev.stat_date = p.stat_date - INTERVAL '1' DAY
  LEFT JOIN (
    SELECT stat_date, project_code, AVG(qc_pass_rate) AS qc_pass_rate
    FROM @dws_annotation_quality_daily
    WHERE stat_date = DATE '${stat_date}'
    GROUP BY stat_date, project_code
  ) q ON q.stat_date = p.stat_date AND q.project_code = p.project_code
  WHERE p.stat_date = DATE '${stat_date}'
) b""",
        expressions={
            "stat_date": "b.stat_date",
            "project_code": "b.project_code",
            "stage_code": "b.stage_code",
            "stage_name": "b.stage_name",
            "stage_order": "b.stage_order",
            "avg_duration_hour": "b.avg_duration_hour",
            "duration_ratio": (
                "CASE WHEN b.total_duration_hour > 0 "
                "THEN b.avg_duration_hour / b.total_duration_hour ELSE CAST(NULL AS DOUBLE) END"
            ),
            "backlog_data_count": "b.backlog_data_count",
            "blocked_over_48h_count": "b.blocked_over_48h_count",
            "throughput_per_hour": "b.throughput_per_hour",
            "capacity_utilization": (
                "CASE WHEN b.resource_core_hour > 0 "
                "THEN CAST(b.output_data_count AS DOUBLE) / b.resource_core_hour "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "bottleneck_rank": "CAST(b.bottleneck_rank AS INT)",
            "bottleneck_level": (
                "CASE\n"
                f"       WHEN b.total_duration_hour > 0 AND b.avg_duration_hour "
                f"/ b.total_duration_hour >= {BOTTLENECK_DURATION_RATIO_THRESHOLD}\n"
                "            AND b.prev_avg_duration_hour > 0\n"
                "            AND (b.avg_duration_hour - b.prev_avg_duration_hour) "
                f"/ b.prev_avg_duration_hour >= {BOTTLENECK_MOM_DETERIORATION_THRESHOLD}\n"
                "         THEN 'P0'\n"
                "       WHEN b.total_duration_hour > 0 AND b.avg_duration_hour "
                f"/ b.total_duration_hour >= {BOTTLENECK_DURATION_RATIO_THRESHOLD} THEN 'P1'\n"
                "       WHEN b.prev_avg_duration_hour > 0\n"
                "            AND (b.avg_duration_hour - b.prev_avg_duration_hour) "
                f"/ b.prev_avg_duration_hour >= {BOTTLENECK_MOM_DETERIORATION_THRESHOLD}\n"
                "         THEN 'P2'\n"
                "       ELSE 'P3' END"
            ),
            "root_cause": (
                "CASE WHEN b.manual_intervention_count > 0 THEN '人力不足'\n"
                "       WHEN b.rerun_count > 0 THEN '算法失败'\n"
                "       WHEN b.backlog_data_count > 0 THEN '上游积压'\n"
                "       WHEN b.resource_core_hour > 0 AND b.throughput_per_hour = 0 THEN '资源不足'\n"
                "       ELSE CAST(NULL AS STRING) END"
            ),
            "trend_vs_prev_day": (
                "CASE WHEN b.prev_avg_duration_hour > 0 "
                "THEN (b.avg_duration_hour - b.prev_avg_duration_hour) "
                "/ b.prev_avg_duration_hour ELSE CAST(NULL AS DOUBLE) END"
            ),
        },
        inferred_notes={
            "blocked_over_48h_count": (
                f"积压判定沿用原文口径：停留超 {PRODUCTION_BLOCKED_ALERT_HOURS} 小时"
                "（[S1-全景] 第五章场景①「120 条在质检环节停留超 48 小时」）"
            ),
            "bottleneck_level": (
                "⚠️ 原文只说「耗时占比超阈值或环比恶化自动标记」未给数值，"
                f"本项目取占比 ≥{BOTTLENECK_DURATION_RATIO_THRESHOLD:.0%}、"
                f"环比恶化 ≥{BOTTLENECK_MOM_DETERIORATION_THRESHOLD:.0%}"
            ),
            "capacity_utilization": "⚠️ 原文未明确，本项目设计：产能利用率 = 完成量 / 资源核时",
            "root_cause": "⚠️ 原文只列了四类根因枚举值，判定规则为本项目设计",
            "affected_batch_count": "DWS 层未保留批次维度，需回 dwd_data_production_chain 统计",
            "suggestion": "优化建议由运营在看板上人工维护，不在批作业里生成",
            "sample_data_id": "代表性样本需从 dwd_data_production_chain 取当日最慢的一条",
            "qc_pass_rate": "已在子查询里聚合，但 catalog 的表 2 字段清单未收录该列",
        },
    ),
    # ---------------------------------------------------------------- 表 3
    MaterializePlan(
        table="ads_badcase_root_cause_distribution",
        from_clause="""(
  SELECT stat_date, project_code, model_version,
         root_cause_category, root_cause_sub_category,
         SUM(new_badcase_count) AS badcase_count,
         SUM(CASE WHEN severity = 'P0' THEN new_badcase_count ELSE 0 END) AS severity_p0_count,
         SUM(related_data_count) AS related_data_count,
         SUM(recollect_data_count) AS suggested_collect_count,
         MAX(top_scene_tag) AS related_scene_tag,
         AVG(wow_change_rate) AS wow_change_rate
  FROM @dws_badcase_statistics
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, project_code, model_version,
           root_cause_category, root_cause_sub_category
) d
LEFT JOIN (
  SELECT stat_date, project_code, model_version,
         SUM(new_badcase_count) AS day_total_badcase
  FROM @dws_badcase_statistics
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, project_code, model_version
) t ON t.stat_date = d.stat_date
   AND t.project_code = d.project_code
   AND t.model_version = d.model_version""",
        expressions={
            "stat_date": "d.stat_date",
            "project_code": "d.project_code",
            "model_version": "d.model_version",
            "root_cause_category": "d.root_cause_category",
            "root_cause_sub_category": "d.root_cause_sub_category",
            "badcase_count": "d.badcase_count",
            "badcase_ratio": (
                "CASE WHEN t.day_total_badcase > 0 "
                "THEN CAST(d.badcase_count AS DOUBLE) / t.day_total_badcase "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "rank_no": (
                "CAST(RANK() OVER (PARTITION BY d.stat_date, d.project_code, d.model_version "
                "ORDER BY d.badcase_count DESC) AS INT)"
            ),
            "trend": (
                "CASE WHEN d.wow_change_rate > 0 THEN 'up'\n"
                "       WHEN d.wow_change_rate < 0 THEN 'down'\n"
                "       ELSE 'flat' END"
            ),
            "wow_change_rate": "d.wow_change_rate",
            "severity_p0_count": "d.severity_p0_count",
            "related_scene_tag": "d.related_scene_tag",
            "related_data_count": "d.related_data_count",
            "suggested_action": (
                "CASE WHEN d.root_cause_category = '标注错误' THEN '标注返工'\n"
                "       WHEN t.day_total_badcase > 0 AND CAST(d.badcase_count AS DOUBLE) "
                f"/ t.day_total_badcase >= {BADCASE_DEMO_PERCEPTION_MISS_RATIO} THEN '定向补采+重训'\n"
                "       WHEN t.day_total_badcase > 0 AND CAST(d.badcase_count AS DOUBLE) "
                f"/ t.day_total_badcase >= {BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO} THEN '定向补采'\n"
                "       ELSE '重训' END"
            ),
            "suggested_collect_count": "d.suggested_collect_count",
        },
        inferred_notes={
            "suggested_action": (
                "⚠️ 原文未给动作判定规则，本项目用原文案例里的两个占比做分档："
                f"感知漏检 {BADCASE_DEMO_PERCEPTION_MISS_RATIO:.0%}、"
                f"夜间行人 {BADCASE_DEMO_NIGHT_PEDESTRIAN_RATIO:.0%}（[S1-05] 第四章 1）"
            ),
            "evaluation_type": "DWS 汇总层丢了评测类型维度，下钻请走 dwd_badcase_detail.evaluation_type",
            "mom_change_rate": "DWS 只提供了周环比 wow_change_rate，月环比需另加 30 日窗口聚合",
            "owner_team": "归属团队来自组织维表，湖仓未接入",
        },
    ),
    # ---------------------------------------------------------------- 表 4
    MaterializePlan(
        table="ads_scene_library_summary",
        from_clause="""(
  SELECT stat_date, scene_type, scene_tag_id,
         MAX(tag_code) AS tag_code,
         MAX(tag_name) AS tag_name,
         COUNT(DISTINCT project_code) AS covered_project_count,
         SUM(total_data_count) AS total_data_count,
         SUM(high_quality_count) AS high_quality_count,
         SUM(badcase_count) AS related_badcase_count,
         AVG(mom_growth_rate) AS badcase_mom_rate,
         SUM(target_count) AS target_count,
         SUM(gap_count) AS gap_count,
         SUM(dataset_ref_count) AS dataset_ref_count
  FROM @dws_scene_distribution
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, scene_type, scene_tag_id
) s
LEFT JOIN (
  SELECT tag_id,
         MAX(gap_level) AS gap_level,
         MAX(gap_status) AS gap_status,
         COUNT(DISTINCT related_collect_task_id) AS mining_task_count
  FROM @dwd_scene_gap_detail
  GROUP BY tag_id
) g ON g.tag_id = s.scene_tag_id""",
        expressions={
            "stat_date": "s.stat_date",
            "scene_type": "s.scene_type",
            "scene_tag_id": "s.scene_tag_id",
            "tag_code": "s.tag_code",
            "tag_name": "s.tag_name",
            "priority": (
                "CASE g.gap_level WHEN 'critical' THEN 'P0' WHEN 'high' THEN 'P1' ELSE 'P2' END"
            ),
            "covered_project_count": "CAST(s.covered_project_count AS INT)",
            "total_data_count": "s.total_data_count",
            "high_quality_count": "s.high_quality_count",
            "high_quality_rate": (
                "CASE WHEN s.total_data_count > 0 "
                "THEN CAST(s.high_quality_count AS DOUBLE) / s.total_data_count "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "related_badcase_count": "s.related_badcase_count",
            "badcase_mom_rate": "s.badcase_mom_rate",
            "target_count": "s.target_count",
            "coverage_rate": (
                "CASE WHEN s.target_count > 0 "
                "THEN CAST(s.total_data_count AS DOUBLE) / s.target_count "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "gap_count": "s.gap_count",
            "coverage_status": (
                "CASE WHEN s.target_count > 0 AND s.total_data_count >= s.target_count "
                "THEN 'COVERED'\n"
                "       WHEN g.mining_task_count > 0 THEN 'FILLING'\n"
                "       ELSE 'GAP' END"
            ),
            "dataset_ref_count": "CAST(s.dataset_ref_count AS INT)",
            "mining_task_count": "CAST(g.mining_task_count AS INT)",
            "trend_7d": (
                "CASE WHEN s.badcase_mom_rate > 0 THEN 'rising'\n"
                "       WHEN s.badcase_mom_rate < 0 THEN 'falling'\n"
                "       ELSE 'flat' END"
            ),
        },
        inferred_notes={
            "coverage_status": (
                "状态机 GAP → FILLING → COVERED 来自 [S1-05] 第四章 2（「定向补采两周后达标、"
                "状态流转 COVERED」）；达标判定 = 总量 ≥ 达标线（案例：150 条 vs 达标线 2,000 条）"
            ),
            "priority": "⚠️ 原文未给优先级规则，本项目按 dwd_scene_gap_detail.gap_level 映射 P0/P1/P2",
            "trend_7d": "⚠️ 原文说「Badcase 逐月上升」，DWS 只有月环比，这里用月环比符号近似 7 日趋势",
            "last_supplement_time": "补采入库时间需回 dwd_collect_clip_detail 按标签取 MAX(采集时间)",
        },
    ),
    # ---------------------------------------------------------------- 表 5
    MaterializePlan(
        table="ads_hard_case_library",
        from_clause="""(
  -- 来源一：评测域（[S1-05] 第四章 3「v3.2 评测挖出 3,200 条难例」）
  SELECT stat_date,
         root_cause_sub_category AS hard_case_category,
         'evaluation' AS source_type,
         model_version,
         project_code,
         SUM(hard_case_count) AS hard_case_count,
         SUM(total_badcase_count) AS badcase_count,
         SUM(related_data_count) AS related_data_count
  FROM @dws_badcase_statistics
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, root_cause_sub_category, model_version, project_code
  UNION ALL
  -- 来源二：回传域（场景④：触发 → 清洗打标 → 自动沉淀为难例）
  SELECT stat_date,
         top_scene_tag AS hard_case_category,
         'trigger' AS source_type,
         CAST(NULL AS STRING) AS model_version,
         project_code,
         SUM(hard_case_cnt) AS hard_case_count,
         CAST(NULL AS BIGINT) AS badcase_count,
         SUM(into_dataset_cnt) AS related_data_count
  FROM @dws_trigger_statistics
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, top_scene_tag, project_code
  UNION ALL
  -- 来源三：挖掘域（挖掘命中直接回补，零采集成本）
  SELECT stat_date,
         task_type AS hard_case_category,
         'mining' AS source_type,
         CAST(NULL AS STRING) AS model_version,
         project_code,
         SUM(hit_data_count) AS hard_case_count,
         CAST(NULL AS BIGINT) AS badcase_count,
         SUM(hit_image_count) AS related_data_count
  FROM @dws_mining_efficiency_daily
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, task_type, project_code
) h
LEFT JOIN (
  -- 训练采纳：难例进了哪个数据集版本（[S1-05] 第四章 3「采纳 2,800 条混入 v3.3 训练集」）
  SELECT model_version, project_code,
         SUM(badcase_data_count) + SUM(mining_data_count) AS adopted_count,
         MAX(dataset_id) AS adopted_dataset_id,
         MAX(dataset_version) AS adopted_dataset_version
  FROM @dwd_dataset_version_detail
  WHERE model_version IS NOT NULL
  GROUP BY model_version, project_code
) a ON a.model_version = h.model_version AND a.project_code = h.project_code""",
        expressions={
            "stat_date": "h.stat_date",
            "hard_case_category": "h.hard_case_category",
            "source_type": "h.source_type",
            "model_version": "COALESCE(h.model_version, 'unknown')",
            "project_code": "h.project_code",
            "hard_case_count": "h.hard_case_count",
            "adopted_count": "a.adopted_count",
            "adoption_rate": (
                "CASE WHEN h.hard_case_count > 0 "
                "THEN CAST(a.adopted_count AS DOUBLE) / h.hard_case_count "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "adopted_dataset_id": "a.adopted_dataset_id",
            "adopted_dataset_version": "a.adopted_dataset_version",
            "badcase_count": "h.badcase_count",
            "related_data_count": "h.related_data_count",
            "closed_loop_status": (
                "CASE WHEN a.adopted_count IS NULL THEN 'pending'\n"
                "       WHEN a.adopted_count > 0 THEN 'adopted'\n"
                "       ELSE 'pending' END"
            ),
        },
        inferred_notes={
            "model_version": "回传/挖掘两个来源没有模型版本，统一落 'unknown'（主键不可为 NULL）",
            "retrain_model_version": "重训后的模型版本需从训练域血缘（dwd_training_task_detail）回填",
            "metric_gain_pp": "指标提升来自 ads_model_version_comparison，不在本作业内计算",
            "miss_rate_drop_pp": (
                "漏检率下降（案例 60%，[S1-05] 第四章 3）同样来自版本对比表，"
                "由评测平台回写，不在本作业内计算"
            ),
            "avg_difficulty_score": "难度分在 dwd_mining_result_detail.hit_score，按类别聚合需另加一路 JOIN",
            "pending_review_count": "候选池待审量在 dwd_mining_data_tag_detail.review_status，属挖掘域口径",
            "closed_loop_status": "⚠️ 原文只给了三个状态值，判定规则为本项目设计",
        },
    ),
    # ---------------------------------------------------------------- 表 6
    MaterializePlan(
        table="ads_data_asset_catalog",
        from_clause="""(
  -- 资产一：数据集（[S1-05] 第六章表 6「城区 NOA 主数据集 v12 被 12 个训练任务引用」）
  SELECT stat_date,
         'dataset' AS asset_type,
         CONCAT(dataset_id, '@', dataset_version) AS asset_id,
         dataset_id,
         dataset_version,
         project_code,
         business_domain,
         data_count,
         storage_size_bytes,
         train_task_ref_count + eval_task_ref_count AS ref_count,
         quality_score
  FROM @dws_dataset_statistics
  WHERE stat_date = DATE '${stat_date}'
  UNION ALL
  -- 资产二：场景库（按场景类型登记为一个资产）
  SELECT stat_date,
         'scene_library' AS asset_type,
         scene_type AS asset_id,
         CAST(NULL AS STRING) AS dataset_id,
         CAST(NULL AS STRING) AS dataset_version,
         MAX(project_code) AS project_code,
         CAST(NULL AS STRING) AS business_domain,
         SUM(total_data_count) AS data_count,
         CAST(NULL AS BIGINT) AS storage_size_bytes,
         CAST(SUM(dataset_ref_count) AS INT) AS ref_count,
         AVG(avg_confidence) * 5.0 AS quality_score
  FROM @dws_scene_distribution
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, scene_type
) a
LEFT JOIN (
  SELECT dataset_id, version,
         MAX(dataset_name) AS dataset_name,
         MAX(release_time) AS release_time
  FROM @dwd_dataset_version_detail
  GROUP BY dataset_id, version
) v ON v.dataset_id = a.dataset_id AND v.version = a.dataset_version""",
        expressions={
            "stat_date": "a.stat_date",
            "asset_type": "a.asset_type",
            "asset_id": "a.asset_id",
            "asset_name": "COALESCE(v.dataset_name, a.asset_id)",
            "dataset_id": "a.dataset_id",
            "dataset_version": "a.dataset_version",
            "project_code": "a.project_code",
            "business_domain": "a.business_domain",
            "data_count": "a.data_count",
            "storage_size_bytes": "a.storage_size_bytes",
            "ref_count": "CAST(a.ref_count AS INT)",
            "ref_count_90d": "CAST(a.ref_count AS INT)",
            "quality_score": "a.quality_score",
            "asset_status": ("CASE WHEN a.ref_count > 0 THEN 'active' ELSE 'idle' END"),
            "archive_suggestion": (
                f"CASE WHEN a.quality_score < {ASSET_LOW_QUALITY_SCORE_THRESHOLD} "
                "AND a.ref_count = 0 THEN 'archive'\n"
                "       ELSE 'keep' END"
            ),
            "register_time": "v.release_time",
        },
        inferred_notes={
            "owner": "原文维度是「资产类型 × 负责人」，但 DWS/DWD 均未落负责人字段，需接组织维表",
            "owner_dept": "同上，负责部门来自组织维表",
            "ref_count_90d": (
                f"⚠️ DWS 只给了累计引用数，近 {ASSET_HOT_WINDOW_DAYS} 天热度需按窗口重算，"
                "此处先与累计值同源，装配阶段替换"
            ),
            "archive_suggestion": (
                f"⚠️ 原文只说「3 个低分老旧数据集长期无人使用 → 标记归档」，未给分数线；"
                f"本项目取质量分 < {ASSET_LOW_QUALITY_SCORE_THRESHOLD} 且引用数 = 0"
            ),
            "storage_tier": "存储分层在 dwd_closed_loop_storage_lifecycle，按资产聚合需另加一路 JOIN",
            "last_used_time": "最近引用时间需从训练/评测任务明细取 MAX(任务开始时间)",
        },
    ),
    # ---------------------------------------------------------------- 表 7
    MaterializePlan(
        table="ads_model_version_comparison",
        from_clause="""(
  SELECT r.model_version,
         r.baseline_model_version,
         r.dataset_id,
         r.dataset_version,
         COALESCE(r.scene_tag, 'ALL') AS scene_type,
         MAX(r.project_code) AS project_code,
         MAX(r.evaluation_type) AS evaluation_type,
         COUNT(*) AS eval_case_cnt,
         CAST(SUM(CASE WHEN r.pass_flag THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) AS pass_rate,
         SUM(CASE WHEN r.badcase_flag THEN 1 ELSE 0 END) AS badcase_cnt,
         CAST(SUM(CASE WHEN r.badcase_flag THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) AS badcase_rate,
         AVG(r.score) AS avg_metric_score
  FROM @dwd_evaluation_result_detail r
  WHERE r.artifact_status = 'active'
    AND CAST(r.evaluate_time AS DATE) = DATE '${stat_date}'
    AND r.baseline_model_version IS NOT NULL
  GROUP BY r.model_version, r.baseline_model_version, r.dataset_id, r.dataset_version,
           COALESCE(r.scene_tag, 'ALL')
) n
LEFT JOIN (
  -- 基线口径：同一评测集、同一场景，跑基线模型版本的那批结果
  SELECT r.model_version,
         r.dataset_id,
         r.dataset_version,
         COALESCE(r.scene_tag, 'ALL') AS scene_type,
         CAST(SUM(CASE WHEN r.pass_flag THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) AS pass_rate,
         CAST(SUM(CASE WHEN r.badcase_flag THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) AS badcase_rate,
         AVG(r.score) AS avg_metric_score
  FROM @dwd_evaluation_result_detail r
  WHERE r.artifact_status = 'active'
  GROUP BY r.model_version, r.dataset_id, r.dataset_version, COALESCE(r.scene_tag, 'ALL')
) base ON base.model_version = n.baseline_model_version
      AND base.dataset_id = n.dataset_id
      AND base.dataset_version = n.dataset_version
      AND base.scene_type = n.scene_type""",
        expressions={
            "model_version": "n.model_version",
            "baseline_model_version": "n.baseline_model_version",
            "dataset_id": "n.dataset_id",
            "dataset_version": "n.dataset_version",
            "scene_type": "n.scene_type",
            "project_code": "n.project_code",
            "evaluation_type": "n.evaluation_type",
            "eval_case_cnt": "n.eval_case_cnt",
            "pass_rate": "n.pass_rate",
            "baseline_pass_rate": "base.pass_rate",
            "pass_rate_diff_pp": "(n.pass_rate - base.pass_rate) * 100.0",
            "badcase_cnt": "n.badcase_cnt",
            "badcase_rate": "n.badcase_rate",
            "baseline_badcase_rate": "base.badcase_rate",
            "avg_metric_score": "n.avg_metric_score",
            "baseline_avg_metric_score": "base.avg_metric_score",
            "regression_flag": (
                "CASE WHEN base.pass_rate IS NULL THEN FALSE\n"
                "       ELSE (n.pass_rate - base.pass_rate) * 100.0 < "
                f"{MODEL_REGRESSION_TOLERANCE_PP_DEFAULT} END"
            ),
            "conclusion": (
                "CASE WHEN base.pass_rate IS NULL THEN '无基线'\n"
                "       WHEN (n.pass_rate - base.pass_rate) * 100.0 > "
                f"{MODEL_REGRESSION_TOLERANCE_PP_DEFAULT} THEN '显著提升'\n"
                "       WHEN (n.pass_rate - base.pass_rate) * 100.0 < "
                f"{MODEL_REGRESSION_TOLERANCE_PP_DEFAULT} THEN '回归'\n"
                "       ELSE '持平' END"
            ),
            "model_type": "CAST(NULL AS STRING)",
            "stat_date": "DATE '${stat_date}'",
        },
        inferred_notes={
            "scene_type": (
                "原文维度「模型版本 × 数据集 × 场景类型」的场景段取 "
                "dwd_evaluation_result_detail.scene_tag，无标签的结果归到 'ALL'"
            ),
            "regression_flag": (
                f"回归判定容差 {MODEL_REGRESSION_TOLERANCE_PP_DEFAULT} pp——"
                "⚠️ 原文只说「标记回归项」未给容差（案例：高速场景 -1.2pp 判回归、"
                "夜间 +8.3pp 判提升，[S1-05] 第五章表 7）"
            ),
            "model_type": "模型类型来自 dwd_training_task_detail，本作业只读评测明细",
        },
    ),
    # ---------------------------------------------------------------- 表 8
    MaterializePlan(
        table="ads_ota_deployment_summary",
        from_clause="""(
  SELECT d.ota_task_id,
         MAX(d.project_code) AS project_code,
         MAX(d.software_version) AS software_version,
         MAX(d.model_version) AS model_version,
         MAX(d.release_channel) AS release_channel,
         MIN(d.push_time) AS publish_time,
         COUNT(*) AS target_vehicle_count,
         SUM(CASE WHEN d.deploy_status = 'success' THEN 1 ELSE 0 END) AS deployed_vehicle_count,
         SUM(CASE WHEN d.deploy_status = 'fail' THEN 1 ELSE 0 END) AS fail_vehicle_count,
         SUM(CASE WHEN d.rollback_flag THEN 1 ELSE 0 END) AS rollback_vehicle_count,
         AVG(COALESCE(d.download_duration_sec, 0) + COALESCE(d.install_duration_sec, 0))
           AS avg_deploy_duration_sec,
         LISTAGG(DISTINCT d.vehicle_model, ',') AS vehicle_model_list
  FROM @dwd_ota_deployment_detail d
  GROUP BY d.ota_task_id
) o
LEFT JOIN (
  SELECT software_version, release_channel, MAX(top_fail_reason) AS top_fail_reason
  FROM @dws_deployment_statistics
  GROUP BY software_version, release_channel
) s ON s.software_version = o.software_version AND s.release_channel = o.release_channel""",
        expressions={
            "ota_task_id": "o.ota_task_id",
            "project_code": "o.project_code",
            "software_version": "o.software_version",
            "model_version": "o.model_version",
            "release_channel": "o.release_channel",
            "publish_time": "o.publish_time",
            "target_vehicle_count": "CAST(o.target_vehicle_count AS INT)",
            "deployed_vehicle_count": "CAST(o.deployed_vehicle_count AS INT)",
            "fail_vehicle_count": "CAST(o.fail_vehicle_count AS INT)",
            "rollback_vehicle_count": "CAST(o.rollback_vehicle_count AS INT)",
            "deploy_progress_rate": (
                "CASE WHEN o.target_vehicle_count > 0 "
                "THEN CAST(o.deployed_vehicle_count + o.fail_vehicle_count AS DOUBLE) "
                "/ o.target_vehicle_count ELSE CAST(NULL AS DOUBLE) END"
            ),
            "deploy_success_rate": (
                "CASE WHEN o.deployed_vehicle_count + o.fail_vehicle_count > 0 "
                "THEN CAST(o.deployed_vehicle_count AS DOUBLE) "
                "/ (o.deployed_vehicle_count + o.fail_vehicle_count) "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "avg_deploy_duration_sec": "o.avg_deploy_duration_sec",
            "top_fail_reason": "s.top_fail_reason",
            "vehicle_model_distribution": (
                "CONCAT('{\"models\":\"', COALESCE(o.vehicle_model_list, ''), '\"}')"
            ),
            "deploy_status": (
                "CASE WHEN o.deployed_vehicle_count + o.fail_vehicle_count >= o.target_vehicle_count "
                "THEN 'finished'\n"
                "       WHEN o.rollback_vehicle_count > 0 THEN 'aborted'\n"
                "       ELSE 'running' END"
            ),
            "stat_time": "CURRENT_TIMESTAMP",
        },
        inferred_notes={
            "task_name": "任务名称在 ods_ota_task，DWD 明细层未冗余",
            "project_name": "项目名称维表尚未登记",
            "vehicle_model_distribution": (
                "⚠️ Flink SQL 无 JSON 聚合函数，车型分布退化为 LISTAGG 逗号清单后包成 JSON 字符串"
            ),
            "deploy_status": "⚠️ 原文只给了三个状态值，判定规则为本项目设计",
        },
    ),
    # ---------------------------------------------------------------- 表 9
    MaterializePlan(
        table="ads_trigger_heatmap",
        from_clause=f"""(
  SELECT CAST(t.trigger_time AS DATE) AS stat_date,
         t.project_code,
         -- ⚠️ 地理网格：DWD 只有经纬度，按 ads.geo 的网格精度取整成网格键
         --    （口径与服务层 ads.geo.grid_id 同源，接真实 GeoHash 时改 geo 一处）
         {_GEO_GRID_SQL} AS geo_grid_id,
         t.trigger_type,
         AVG(t.gps_lat) AS grid_center_lat,
         AVG(t.gps_lon) AS grid_center_lon,
         MAX(t.city_code) AS city_code,
         MAX(t.road_type) AS road_type,
         COUNT(*) AS trigger_cnt,
         COUNT(DISTINCT t.vehicle_code) AS vehicle_cnt,
         SUM(CASE WHEN t.is_hard_case THEN 1 ELSE 0 END) AS hard_case_cnt,
         MAX(t.scene_tag) AS top_scene_tag,
         MIN(t.data_id) AS sample_data_id
  FROM @dwd_vehicle_trigger_detail t
  WHERE CAST(t.trigger_time AS DATE) = DATE '${{stat_date}}'
    AND t.gps_lat IS NOT NULL AND t.gps_lon IS NOT NULL
  GROUP BY CAST(t.trigger_time AS DATE), t.project_code, t.trigger_type,
           {_GEO_GRID_SQL}
) g""",
        expressions={
            "stat_date": "g.stat_date",
            "project_code": "g.project_code",
            "geo_grid_id": "g.geo_grid_id",
            "trigger_type": "g.trigger_type",
            "grid_center_lat": "g.grid_center_lat",
            "grid_center_lon": "g.grid_center_lon",
            "city_code": "g.city_code",
            "road_type": "g.road_type",
            "trigger_cnt": "g.trigger_cnt",
            "heat_level": heat_level_sql("g.trigger_cnt"),
            "vehicle_cnt": "g.vehicle_cnt",
            "hard_case_cnt": "g.hard_case_cnt",
            "top_scene_tag": "g.top_scene_tag",
            "sample_data_id": "g.sample_data_id",
        },
        inferred_notes={
            "geo_grid_id": (
                f"⚠️ catalog 说网格是 GeoHash，DWD 只落了经纬度；本项目按 "
                f"{GEO_GRID_PRECISION_DEGREES} 度（纬向约 1.1km）取整成网格键，"
                "口径定义在 ads.geo.grid_id，接入真实 GeoHash 后改那一处即可"
            ),
            "heat_level": (
                f"⚠️ 原文只说热力等级 "
                f"{TRIGGER_HEAT_LEVEL_MIN}~{TRIGGER_HEAT_LEVEL_MAX}，未给分档阈值；"
                f"本项目按触发次数 "
                f"{'/'.join(str(t) for t in TRIGGER_HEAT_LEVEL_THRESHOLDS)} 分五档"
                "（口径定义在 ads.geo.heat_level，与服务层同源）"
            ),
            "city_name": "城市名称需接行政区维表（DWD 只有 city_code）",
            "avg_vehicle_speed_kph": "DWD 触发明细未落车速字段",
            "shadow_divergence_cnt": "影子模式分歧数在 dwd_shadow_mode_detail，按网格聚合需另加一路 JOIN",
            "top_scene_tag_cnt": "取 TOP1 标签的次数需要二次聚合，本作业先留空",
        },
    ),
    # ---------------------------------------------------------------- 表 10
    MaterializePlan(
        table="ads_storage_cost_dashboard",
        from_clause="""(
  SELECT stat_date, storage_media, lifecycle_stage, data_type,
         SUM(total_capacity_tb) AS total_capacity_tb,
         SUM(daily_cost_yuan) AS daily_cost_yuan,
         SUM(preheat_volume_tb) AS preheat_volume_tb,
         SUM(tier_down_volume_tb) AS tier_down_volume_tb,
         SUM(evict_volume_tb) AS evict_volume_tb,
         SUM(delete_volume_tb) AS delete_volume_tb,
         SUM(baseline_cost_yuan) AS baseline_cost_yuan,
         SUM(saved_cost_yuan) AS saved_cost_yuan,
         MAX(nas_peak_usage) AS nas_peak_usage,
         AVG(preheat_hit_rate) AS preheat_hit_rate,
         SUM(archive_restore_count) AS archive_restore_count,
         AVG(cost_mom_rate) AS cost_mom_rate
  FROM @dws_closed_loop_storage_cost_daily
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, storage_media, lifecycle_stage, data_type
) c
LEFT JOIN (
  SELECT stat_date, SUM(total_capacity_tb) AS lake_capacity_tb
  FROM @dws_closed_loop_storage_cost_daily
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date
) lake ON lake.stat_date = c.stat_date
LEFT JOIN (
  -- 月成本 = 本月 1 号至 T 日的日成本累加（[S1-05] 表 10「月末复盘」口径）
  SELECT storage_media, lifecycle_stage, data_type,
         SUM(daily_cost_yuan) AS month_cost_yuan
  FROM @dws_closed_loop_storage_cost_daily
  WHERE stat_date BETWEEN DATE '${month_start}' AND DATE '${stat_date}'
  GROUP BY storage_media, lifecycle_stage, data_type
) m ON m.storage_media = c.storage_media
   AND m.lifecycle_stage = c.lifecycle_stage
   AND m.data_type = c.data_type
LEFT JOIN (
  SELECT storage_media, lifecycle_stage, data_type,
         SUM(total_capacity_tb) AS prev_capacity_tb
  FROM @dws_closed_loop_storage_cost_daily
  WHERE stat_date = DATE '${stat_date}' - INTERVAL '1' DAY
  GROUP BY storage_media, lifecycle_stage, data_type
) p ON p.storage_media = c.storage_media
   AND p.lifecycle_stage = c.lifecycle_stage
   AND p.data_type = c.data_type""",
        expressions={
            "stat_date": "c.stat_date",
            "storage_media": "c.storage_media",
            "lifecycle_stage": "c.lifecycle_stage",
            "data_type": "c.data_type",
            "total_capacity_tb": "c.total_capacity_tb",
            "capacity_ratio": (
                "CASE WHEN lake.lake_capacity_tb > 0 "
                "THEN c.total_capacity_tb / lake.lake_capacity_tb ELSE CAST(NULL AS DOUBLE) END"
            ),
            "month_cost_yuan": "m.month_cost_yuan",
            "unit_price_yuan_gb_month": (
                "CASE WHEN c.total_capacity_tb > 0 "
                "THEN c.daily_cost_yuan * 30.0 / (c.total_capacity_tb * 1024.0) "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "preheat_volume_tb": "c.preheat_volume_tb",
            "tier_down_volume_tb": "c.tier_down_volume_tb",
            "evict_volume_tb": "c.evict_volume_tb",
            "delete_volume_tb": "c.delete_volume_tb",
            "baseline_cost_yuan": "c.baseline_cost_yuan",
            "saved_cost_yuan": "c.saved_cost_yuan",
            "nas_peak_usage": "c.nas_peak_usage",
            "preheat_hit_rate": "c.preheat_hit_rate",
            "archive_restore_count": "CAST(c.archive_restore_count AS INT)",
            "cost_mom_rate": "c.cost_mom_rate",
            "capacity_mom_rate": (
                "CASE WHEN p.prev_capacity_tb > 0 "
                "THEN (c.total_capacity_tb - p.prev_capacity_tb) / p.prev_capacity_tb "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "budget_alert_flag": (
                f"COALESCE(c.cost_mom_rate, 0.0) > {STORAGE_COST_MOM_ALERT_THRESHOLD}\n"
                f"       OR COALESCE(c.nas_peak_usage, 0.0) > {NAS_PEAK_USAGE_ALERT_THRESHOLD}"
            ),
        },
        inferred_notes={
            "unit_price_yuan_gb_month": (
                "⚠️ 原文只给了介质相对单价（NAS 为 OSS 标准的 8~10 倍、低频 0.5x、归档 0.15x），"
                "绝对单价由日成本反算：日成本 × 30 天 ÷ 容量(GB)"
            ),
            "budget_alert_flag": (
                f"告警线沿用原文：成本环比 >{STORAGE_COST_MOM_ALERT_THRESHOLD:.0%} 自动告警"
                f"（[S1-全景] 第八章③），NAS 峰值使用率 >{NAS_PEAK_USAGE_ALERT_THRESHOLD:.0%}"
                "（catalog DWS 口径）"
            ),
            "month_cost_yuan": "月成本按本月累计日成本求和，与原文「月末复盘」口径一致",
        },
    ),
    # ---------------------------------------------------------------- 表 11
    MaterializePlan(
        table="ads_mining_tag_dashboard",
        from_clause="""(
  SELECT CAST(t.first_tag_time AS DATE) AS stat_date,
         t.project_code,
         t.tag_id,
         MAX(t.tag_category) AS tag_category,
         COUNT(DISTINCT t.data_id) AS data_count,
         SUM(CASE WHEN t.tag_source = 'collect' THEN 1 ELSE 0 END) AS collect_source_count,
         SUM(CASE WHEN t.tag_source = 'rule' THEN 1 ELSE 0 END) AS rule_source_count,
         SUM(CASE WHEN t.tag_source = 'vlm' THEN 1 ELSE 0 END) AS vlm_source_count,
         AVG(t.confidence) AS avg_confidence
  FROM @dwd_mining_data_tag_detail t
  WHERE CAST(t.first_tag_time AS DATE) = DATE '${stat_date}'
  GROUP BY CAST(t.first_tag_time AS DATE), t.project_code, t.tag_id
) m
LEFT JOIN @dwd_mining_tag_dict_detail d ON d.tag_id = m.tag_id
LEFT JOIN (
  SELECT stat_date, project_code, tag_category,
         SUM(tagged_data_count) AS category_data_count,
         AVG(data_coverage_ratio) AS data_coverage_ratio
  FROM @dws_mining_tag_coverage_daily
  WHERE stat_date = DATE '${stat_date}'
  GROUP BY stat_date, project_code, tag_category
) cov ON cov.stat_date = m.stat_date
     AND cov.project_code = m.project_code
     AND cov.tag_category = m.tag_category
LEFT JOIN (
  SELECT tag_id, MAX(gap_level) AS gap_level
  FROM @dwd_scene_gap_detail
  GROUP BY tag_id
) gap ON gap.tag_id = m.tag_id
LEFT JOIN (
  SELECT CAST(first_tag_time AS DATE) AS stat_date, project_code, tag_id,
         COUNT(DISTINCT data_id) AS prev_data_count
  FROM @dwd_mining_data_tag_detail
  WHERE CAST(first_tag_time AS DATE) = DATE '${stat_date}' - INTERVAL '1' DAY
  GROUP BY CAST(first_tag_time AS DATE), project_code, tag_id
) prev ON prev.project_code = m.project_code AND prev.tag_id = m.tag_id""",
        expressions={
            "stat_date": "m.stat_date",
            "project_code": "m.project_code",
            "tag_id": "m.tag_id",
            "tag_name": "d.tag_name",
            "tag_category": "m.tag_category",
            "parent_tag_id": "d.parent_tag_id",
            "tag_level": "d.tag_level",
            "tag_status": "d.tag_status",
            "data_count": "m.data_count",
            "data_ratio": (
                "CASE WHEN cov.category_data_count > 0 "
                "THEN CAST(m.data_count AS DOUBLE) / cov.category_data_count "
                "ELSE CAST(NULL AS DOUBLE) END"
            ),
            "collect_source_count": "m.collect_source_count",
            "rule_source_count": "m.rule_source_count",
            "vlm_source_count": "m.vlm_source_count",
            "avg_confidence": "m.avg_confidence",
            "coverage_ratio": "cov.data_coverage_ratio",
            "gap_level": "gap.gap_level",
            "rank_in_category": (
                "CAST(RANK() OVER (PARTITION BY m.stat_date, m.tag_category "
                "ORDER BY m.data_count DESC) AS INT)"
            ),
            "dod_change_ratio": (
                "CASE WHEN prev.prev_data_count > 0 "
                "THEN (m.data_count - prev.prev_data_count) "
                "/ CAST(prev.prev_data_count AS DOUBLE) ELSE CAST(NULL AS DOUBLE) END"
            ),
        },
        inferred_notes={
            "image_count": "图片级标签在 dwd_mining_image_tag_detail，本作业只汇 clip 级",
            "image_ratio": "同上，图片占比需图片级标签表参与",
            "coverage_ratio": "⚠️ DWS 的覆盖率是类别级，这里按类别值下发到标签行（近似口径）",
            "collect_source_count": (
                "三来源取值 collect/rule/vlm，对齐原文「三来源（采集/规则/模型）标签量构成」"
                "（[S1-05] 第六章表 11）与 dws_mining_tag_coverage_daily 的三个计数字段"
            ),
        },
    ),
]

PLANS: Final[dict[str, MaterializePlan]] = {p.table: p for p in _PLAN_LIST}


# ===========================================================================
# 渲染
# ===========================================================================


def _null_expr(col: Column) -> str:
    return f"CAST(NULL AS {col.type})"


def render_flink_sql(table: str) -> str:
    """渲染某张 ADS 表的第 ① 段 Flink 批作业 SQL。

    字段清单来自 catalog 的 TableSpec（含自动追加的系统字段），
    每个字段的表达式来自本模块的上游映射；没映射上的字段渲染成 NULL 占位并标注原因。

    Args:
        table: ADS 表名。

    Returns:
        可直接交给 Flink SQL Client / SQL Gateway 执行的批作业脚本。

    Raises:
        UnknownTableError: 表不在产品矩阵内。
        BackendUnavailableError: catalog 里找不到该表。
        KeyError: 本模块尚未为该表登记上游映射。
    """
    product = get_product(table)
    spec = load_table_spec(table)
    try:
        plan = PLANS[table]
    except KeyError:
        raise KeyError(
            f"{table} 尚未登记上游映射，请在 ads.materialize._PLAN_LIST 中补充"
        ) from None

    cfg = settings().flink
    lines: list[str] = [
        "-- ===========================================================================",
        f"-- {product.ordinal}. {product.title_cn}  →  {table}",
        f"-- 服务对象: {product.serves_cn}",
        f"-- 业务主题: {product.theme.name_cn}",
        f"-- 计算维度: {product.dimensions_cn}",
        f"-- 核心指标: {' / '.join(product.core_metrics_cn)}",
        f"-- 落表链路: {' + '.join(product.source_tables)} → {table}",
        "-- 刷新方式: T+1 批加工（${stat_date} 由调度器注入，yyyy-MM-dd）",
        "--",
        "-- ⚠️ 本文件由 adas_lakehouse.ads.materialize 生成，请勿手工编辑：",
        "--      python -m adas_lakehouse.ads.materialize --write",
        "-- ⚠️ 原文未公开 ADS 加工 SQL，上游字段映射为本项目按 DWS/DWD 实际字段推断，",
        "--    映射不到的字段以 CAST(NULL AS ...) 占位并在行尾标注原因。",
        "-- ===========================================================================",
        "",
        "SET 'execution.runtime-mode' = 'batch';",
        f"SET 'pipeline.name' = 'ads_t1_{table}';",
        f"SET 'parallelism.default' = '{cfg.parallelism}';",
        "-- 批模式下 sink 到 Paimon 主键表即 UPSERT，重跑同一天幂等覆盖",
        "",
    ]

    cols = spec.all_columns()
    col_list = ",\n".join(f"  `{c.name}`" for c in cols)
    lines.append(f"INSERT INTO {_q(table)} (\n{col_list}\n)")
    lines.append("SELECT")

    select_parts: list[tuple[str, str]] = []
    for col in cols:
        if col.name in ("_ingest_time", "update_time"):
            expr = "CURRENT_TIMESTAMP"
            note = ""
        elif col.name in plan.expressions:
            expr = plan.expressions[col.name]
            note = plan.inferred_notes.get(col.name, "")
        else:
            expr = _null_expr(col)
            note = plan.inferred_notes.get(col.name, "上游暂无对应口径")
        select_parts.append((f"  {expr} AS `{col.name}`", note.replace("\n", " ")))
    # 逗号必须写在行注释**之前**：`-- 注释` 会吃掉它后面的一切，
    # 逗号落进注释里整段 SELECT 就不是合法 SQL 了。
    rendered: list[str] = []
    for index, (part, note) in enumerate(select_parts):
        line = part if index == len(select_parts) - 1 else part + ","
        rendered.append(f"{line}  -- {note}" if note else line)
    lines.append("\n".join(rendered))

    lines.append(f"FROM {plan.from_clause}")
    if plan.where:
        lines.append(f"WHERE {plan.where}")
    lines.append(";")
    return _expand("\n".join(lines)) + "\n"


def render_all_flink_sql() -> dict[str, str]:
    """渲染全部 11 个批作业，返回 {文件名: SQL 内容}。"""
    return {f"ads_{p.table[4:]}.sql": render_flink_sql(p.table) for p in PRODUCTS}


def write_sql_files(project_root: str | Path | None = None) -> list[Path]:
    """把两段 SQL 落盘。

    Args:
        project_root: 项目根目录（含 flink/ 与 ddl/ 两个子目录）。
            不传则按本文件位置上溯到 apps/adas-closed-loop-lakehouse。

    Returns:
        写出的文件路径列表。

    Raises:
        BackendUnavailableError: catalog 不可用导致无法渲染。
    """
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
    flink_dir = root / "flink" / "sql"
    ddl_dir = root / "ddl"
    flink_dir.mkdir(parents=True, exist_ok=True)
    ddl_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename, sql in render_all_flink_sql().items():
        path = flink_dir / filename
        path.write_text(sql, encoding="utf-8")
        written.append(path)

    ddl_path = ddl_dir / "starrocks_ads.sql"
    ddl_path.write_text(render_all_starrocks_ddl(), encoding="utf-8")
    written.append(ddl_path)
    return written


# ===========================================================================
# 调度编排
# ===========================================================================


class SqlSubmitter(Protocol):
    """SQL 提交端口。生产实现应对接 Flink SQL Gateway 与 StarRocks 连接。"""

    def submit(self, name: str, sql: str) -> str: ...


class ScriptExportSubmitter:
    """把渲染好的 SQL 写到目录里，交给外部调度器（Airflow/DolphinScheduler）执行。

    ⚠️ 原文未明确，本项目设计：原文只说 ADS 是「T+1 批加工」，没有指定调度系统。
    本项目不绑定任何调度器——默认实现只负责产出可执行脚本，
    真正的提交由使用方注入自己的 :class:`SqlSubmitter`（对接 Flink SQL Gateway
    ``{gateway}`` 或 StarRocks ``{fe}``）。
    """

    def __init__(self, out_dir: str | Path) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def submit(self, name: str, sql: str) -> str:
        path = self.out_dir / f"{name}.sql"
        path.write_text(sql, encoding="utf-8")
        return str(path)


@dataclass(slots=True)
class AdsMaterializer:
    """T+1 物化编排：按依赖顺序产出 11 张表两段 SQL，并交给提交器执行。

    Args:
        submitter: SQL 提交端口；不传则只能渲染，调 :meth:`run` 会抛
            :class:`errors.ServiceUnavailableError`。

    Examples:
        典型用法（22 个作业 = 11 个 Flink 批作业 + 11 条内表物化语句）::

            m = AdsMaterializer(ScriptExportSubmitter("/var/adas/ads_t1"))
            handles = m.run("2026-08-28")
    """

    submitter: SqlSubmitter | None = None

    def plan(self, stat_date: str) -> list[tuple[str, str]]:
        """产出 (作业名, SQL) 列表：先 11 个 Flink 批作业，再 11 条内表物化。

        Args:
            stat_date: 统计日期，yyyy-MM-dd。

        Returns:
            按执行顺序排好的作业列表——湖内口径先固化，内表出口后刷新。
        """
        month_start = f"{stat_date[:7]}-01"

        def bind(sql: str) -> str:
            return sql.replace("${stat_date}", stat_date).replace("${month_start}", month_start)

        jobs: list[tuple[str, str]] = []
        for product in PRODUCTS:
            jobs.append((f"flink_{product.table}", bind(render_flink_sql(product.table))))
        from .schema import starrocks_table  # 局部 import，避免循环依赖

        for product in PRODUCTS:
            jobs.append(
                (
                    f"starrocks_{product.table}",
                    bind(starrocks_table(product.table).render_insert_overwrite()),
                )
            )
        return jobs

    def run(self, stat_date: str) -> list[str]:
        """执行 T+1 物化。

        Raises:
            ServiceUnavailableError: 没注入提交器。
        """
        if self.submitter is None:
            raise ServiceUnavailableError(
                "AdsMaterializer 没有注入 SqlSubmitter：ADS 服务层不绑定调度器，"
                "请注入 ScriptExportSubmitter（导出脚本交外部调度）"
                "或你自己的 Flink SQL Gateway / StarRocks 提交器"
            )
        return [self.submitter.submit(name, sql) for name, sql in self.plan(stat_date)]


if ScriptExportSubmitter.__doc__:  # pragma: no cover - 纯文档处理
    ScriptExportSubmitter.__doc__ = ScriptExportSubmitter.__doc__.format(
        gateway=settings().flink.sql_gateway_url,
        fe=f"{settings().starrocks.fe_host}:{settings().starrocks.query_port}",
    )


def _render_table(rows: list[dict[str, str]]) -> str:
    """把自检矩阵渲染成 Markdown 表格。"""
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(r[h] for h in headers) + " |" for r in rows)
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    """命令行入口：两项自检（catalog 对账 + 服务化出口）、渲染并（可选）写出 SQL。

    两项自检都是「开箱即用」的护栏，缺一不可，所以都在这里跑一次：

      · :func:`schema.verify_against_catalog`——服务层按老口径查、湖表已经改了；
      · :func:`services.verify_service_exits`——11 张表有没有表是**查得到但没出口**的，
        以及 [S1-全景] 第九章的代表 API 有没有既没实现也没登记去处。

    任一自检有问题即非零退出，CI 直接当断言用。
    """
    parser = argparse.ArgumentParser(
        prog="python -m adas_lakehouse.ads.materialize",
        description="ADS 数据产品矩阵：产品矩阵自检 + 服务化出口自检 + 两段 SQL 生成",
    )
    parser.add_argument("--write", action="store_true", help="把 SQL 写入 flink/sql/ 与 ddl/")
    parser.add_argument("--root", default=None, help="项目根目录（默认自动推断）")
    args = parser.parse_args(argv)

    from .products import render_matrix
    from .schema import verify_against_catalog
    from .services import service_exit_matrix, verify_service_exits

    print(render_matrix())
    print()
    problems = verify_against_catalog()
    if problems:
        print("⚠️ 产品矩阵与 catalog 注册表存在偏差：")
        for table, issues in problems.items():
            for issue in issues:
                print(f"  · {table}: {issue}")
    else:
        print(f"✅ 产品矩阵与 catalog 注册表一致（{len(PRODUCTS)} 张 ADS 表）")

    print()
    print(_render_table(service_exit_matrix()))
    print()
    exit_problems = verify_service_exits()
    if exit_problems:
        print("⚠️ 服务化出口自检未通过：")
        for check, issues in exit_problems.items():
            for issue in issues:
                print(f"  · {check}: {issue}")
    else:
        print(f"✅ {len(PRODUCTS)} 张 ADS 表全部有服务化出口，原文代表 API 已全部落地或登记去处")

    if args.write:
        written = write_sql_files(args.root)
        print(f"\n已写出 {len(written)} 个 SQL 文件：")
        for path in written:
            print(f"  · {path}")
    return 1 if (problems or exit_problems) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
