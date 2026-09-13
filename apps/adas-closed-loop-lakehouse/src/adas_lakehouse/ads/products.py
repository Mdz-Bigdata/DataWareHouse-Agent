"""11 张 ADS 表的数据产品矩阵：服务对象 × 计算维度 × 业务场景。

来源：[S1-05]《11 张 ADS 数据闭环表开箱即用：智驾数据产品矩阵全览》第二~六章，
配合 [S1-全景] 第五章（六大场景落表链路）与第九章（六项闭环业务服务）。
两篇的完整出处见 constants 模块 docstring。

这一层是「表 → 产品」的元数据：每张 ADS 表回答哪个业务问题、给谁用、按什么维度算、
上游从哪来、被哪项闭环业务服务消费。它不是文档的副本，而是被真正使用的运行时元数据：

  · query.AdsQueryService 用 ``PRODUCTS`` 做表白名单（出口收敛到这 11 张）
  · materialize 用 ``source_tables`` 渲染 T+1 物化作业与依赖顺序
  · services 用 ``consumed_by`` 反查「这项服务读了哪几张表」
  · gateway 的审计日志用 ``theme`` / ``serves`` 做调用归因

设计原则（[S1-05] 第六章「设计启示」原话）：每张表只回答一个业务问题，
口径在加工时固化、答案在查询时直取。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from ..domains import DataDomain
from .errors import UnknownTableError

__all__ = [
    "BusinessPlatform",
    "AdsTheme",
    "ClosedLoopService",
    "AdsProduct",
    "PRODUCTS",
    "ADS_TABLE_NAMES",
    "get_product",
    "products_by_theme",
    "products_by_platform",
    "products_by_service",
    "matrix_rows",
    "render_matrix",
]


class BusinessPlatform(str, Enum):
    """应用层的 9 大平台 + 监控大屏（[S1-全景] 第二章分层表）。

    原文原话：「9 大平台（数据管理 / 标注 / 训练 / 评测 / 仿真 / 问题分析 / 数据挖掘 /
    车云）+ 监控大屏；既消费数据产品，又持续产生业务数据」。

    [S1-05] 第二章的 11 张表服务对象只落在其中 6 个平台上，
    对应常量 ADS_SERVED_PLATFORM_COUNT = 6。
    """

    DATA_MANAGEMENT = "data_management"
    ANNOTATION = "annotation"
    TRAINING = "training"
    EVALUATION = "evaluation"
    SIMULATION = "simulation"
    ISSUE_ANALYSIS = "issue_analysis"
    DATA_MINING = "data_mining"
    VEHICLE_CLOUD = "vehicle_cloud"
    MONITOR_DASHBOARD = "monitor_dashboard"

    @property
    def name_cn(self) -> str:
        return _PLATFORM_CN[self]


_PLATFORM_CN: Final[dict[BusinessPlatform, str]] = {
    BusinessPlatform.DATA_MANAGEMENT: "数据管理平台",
    BusinessPlatform.ANNOTATION: "标注平台",
    BusinessPlatform.TRAINING: "训练平台",
    BusinessPlatform.EVALUATION: "评测平台",
    BusinessPlatform.SIMULATION: "仿真平台",
    BusinessPlatform.ISSUE_ANALYSIS: "问题分析平台",
    BusinessPlatform.DATA_MINING: "数据挖掘平台",
    BusinessPlatform.VEHICLE_CLOUD: "车云平台",
    BusinessPlatform.MONITOR_DASHBOARD: "监控大屏",
}


class AdsTheme(str, Enum):
    """六大业务主题（[S1-05] 第二章「按业务主题归成六组」原话）。

    闭环健康度（1、2）、数据质量资产（3、4、5）、资产运营（6）、
    模型迭代与上车（7、8、9）、成本治理（10）、挖掘运营（11）。
    """

    CLOSED_LOOP_HEALTH = "closed_loop_health"
    DATA_QUALITY_ASSET = "data_quality_asset"
    ASSET_OPERATION = "asset_operation"
    MODEL_ITERATION_ROLLOUT = "model_iteration_rollout"
    COST_GOVERNANCE = "cost_governance"
    MINING_OPERATION = "mining_operation"

    @property
    def name_cn(self) -> str:
        return _THEME_CN[self]


_THEME_CN: Final[dict[AdsTheme, str]] = {
    AdsTheme.CLOSED_LOOP_HEALTH: "闭环健康度",
    AdsTheme.DATA_QUALITY_ASSET: "数据质量资产",
    AdsTheme.ASSET_OPERATION: "资产运营",
    AdsTheme.MODEL_ITERATION_ROLLOUT: "模型迭代与上车",
    AdsTheme.COST_GOVERNANCE: "成本治理",
    AdsTheme.MINING_OPERATION: "挖掘运营",
}


class ClosedLoopService(str, Enum):
    """六项闭环业务服务（[S1-全景] 第九章服务表，逐字对应）。

    | 业务服务 | 回答的业务问题 | 代表 API |
    | 🚦 数据生产追踪 | 这批数据到哪一步了？哪个环节最慢？ | production/batch/.../progress |
    | 🎯 场景检索与样本圈选 | 缺雨天数据，多久能从库里圈出来？ | scene/search · scene/curate |
    | 📦 数据集版本与交付 | V3 的数据到底从哪来？谁用了它？ | dataset/.../composition |
    | 🔁 模型迭代评测 | 效果回退是数据问题还是模型问题？ | model/compare · badcase/root-cause |
    | 🔄 回传与挖掘闭环 | 触发到进训练集多久？缺口补上了吗？ | trigger/.../closed-loop · scene-gap/status |
    | 🧬 全链路血缘追溯 | Badcase 数据从哪来？影响了哪些模型？ | lineage/business/trace · lineage/impact |
    """

    PRODUCTION_TRACKING = "production_tracking"
    SCENE_SEARCH_CURATION = "scene_search_curation"
    DATASET_VERSION_DELIVERY = "dataset_version_delivery"
    MODEL_ITERATION_EVALUATION = "model_iteration_evaluation"
    TRIGGER_MINING_CLOSED_LOOP = "trigger_mining_closed_loop"
    LINEAGE_TRACE = "lineage_trace"

    @property
    def name_cn(self) -> str:
        return _SERVICE_CN[self][0]

    @property
    def question_cn(self) -> str:
        """该服务回答的业务问题（[S1-全景] 第九章表第二列原文）。"""
        return _SERVICE_CN[self][1]

    @property
    def representative_apis(self) -> tuple[str, ...]:
        """代表 API（[S1-全景] 第九章表第三列原文）。"""
        return _SERVICE_CN[self][2]


_SERVICE_CN: Final[dict[ClosedLoopService, tuple[str, str, tuple[str, ...]]]] = {
    ClosedLoopService.PRODUCTION_TRACKING: (
        "🚦 数据生产追踪",
        "这批数据到哪一步了？哪个环节最慢？",
        ("production/batch/.../progress",),
    ),
    ClosedLoopService.SCENE_SEARCH_CURATION: (
        "🎯 场景检索与样本圈选",
        "缺雨天数据，多久能从库里圈出来？",
        ("scene/search", "scene/curate"),
    ),
    ClosedLoopService.DATASET_VERSION_DELIVERY: (
        "📦 数据集版本与交付",
        "V3 的数据到底从哪来？谁用了它？",
        ("dataset/.../composition",),
    ),
    ClosedLoopService.MODEL_ITERATION_EVALUATION: (
        "🔁 模型迭代评测",
        "效果回退是数据问题还是模型问题？",
        ("model/compare", "badcase/root-cause"),
    ),
    ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP: (
        "🔄 回传与挖掘闭环",
        "触发到进训练集多久？缺口补上了吗？",
        ("trigger/.../closed-loop", "scene-gap/status"),
    ),
    ClosedLoopService.LINEAGE_TRACE: (
        "🧬 全链路血缘追溯",
        "Badcase 数据从哪来？问题数据影响了哪些模型？",
        ("lineage/business/trace", "lineage/impact"),
    ),
}


@dataclass(frozen=True, slots=True)
class AdsProduct:
    """一张 ADS 表作为「数据产品」的完整描述。

    字段与 [S1-05] 的对应关系：
      ordinal / table / title_cn      第二章全景表的「序 / 表名」
      serves                          第二章全景表的「主要服务对象」
      theme                           第二章「按业务主题归成六组」
      dimensions_cn                   第三~六章每张表的「按 xx × xx 加工」原话
      core_metrics_cn                 第三~六章每张表的核心指标原话
      scenario_cn                     第三~六章的真实业务场景案例（含原文数字）
      source_tables                   [S1-全景] 第五章「核心落表链路」+ catalog 注册表
      consumed_by                     本项目把该表挂到哪几项闭环业务服务下
      key_columns                     catalog 注册表里该表的主键，也是查询的下钻键
    """

    ordinal: int
    table: str
    title_cn: str
    theme: AdsTheme
    serves: tuple[BusinessPlatform, ...]
    domain: DataDomain
    dimensions_cn: str
    core_metrics_cn: tuple[str, ...]
    scenario_cn: str
    source_tables: tuple[str, ...]
    consumed_by: tuple[ClosedLoopService, ...]
    key_columns: tuple[str, ...]
    #: 该产品的时间轴列——用于「取最新一天」的默认过滤；按 ota_task_id 汇总的表没有
    date_column: str | None = "stat_date"
    notes: str = ""

    @property
    def serves_cn(self) -> str:
        return " · ".join(p.name_cn for p in self.serves)


# ===========================================================================
# 11 张表 —— 顺序与 [S1-05] 第二章全景表的「序」一致
# ===========================================================================

PRODUCTS: Final[tuple[AdsProduct, ...]] = (
    AdsProduct(
        ordinal=1,
        table="ads_closed_loop_dashboard",
        title_cn="闭环大盘指标表",
        theme=AdsTheme.CLOSED_LOOP_HEALTH,
        serves=(BusinessPlatform.MONITOR_DASHBOARD, BusinessPlatform.DATA_MANAGEMENT),
        domain=DataDomain.CLOSED_LOOP,
        dimensions_cn="统计日期 × 项目代码",
        core_metrics_cn=(
            "总数据量与月度新增",
            "平均闭环耗时（车端触发 → OTA 部署各环节时长汇总）",
            "Badcase 解决率",
            "各状态数据量分布",
            "数据增长率与效率提升率",
        ),
        scenario_cn=(
            "大屏首屏用 KPI 指标卡 + 多线趋势图，一眼看闭环转得快不快、质量好不好。"
            "两级下钻的上层：大盘发现闭环平均耗时 216 小时 → 下钻产线瓶颈分析表定位到评测环节 →"
            "一周优化到 96 小时（[S1-05] 第三章 + 第六章设计启示）"
        ),
        source_tables=("dws_closed_loop_efficiency", "dws_data_contribution"),
        consumed_by=(ClosedLoopService.PRODUCTION_TRACKING,),
        key_columns=("stat_date", "project_code"),
        notes="大盘看「闭环慢不慢」，bottleneck_stage 字段是下钻表 2 的入口",
    ),
    AdsProduct(
        ordinal=2,
        table="ads_production_bottleneck_analysis",
        title_cn="产线瓶颈分析表",
        theme=AdsTheme.CLOSED_LOOP_HEALTH,
        serves=(BusinessPlatform.DATA_MANAGEMENT, BusinessPlatform.MONITOR_DASHBOARD),
        domain=DataDomain.PRODUCTION,
        dimensions_cn="统计日期 × 项目代码 × 产线环节（上云/前处理/标注/质检）",
        core_metrics_cn=(
            "各环节平均耗时",
            "瓶颈环节标识（耗时占比超阈值或环比恶化自动标记）",
            "积压数据量",
            "吞吐量/质检通过率",
        ),
        scenario_cn=(
            "某项目批次 3,200 条 clip 完成上云，其中 120 条在质检环节停留超 48 小时——"
            "「这批数据到哪一步了？哪个环节最慢？」不再需要问三个平台拼答案"
            "（[S1-全景] 第五章场景①）"
        ),
        source_tables=(
            "dws_production_efficiency_daily",
            "dws_annotation_quality_daily",
            "dwd_data_production_chain",
        ),
        consumed_by=(ClosedLoopService.PRODUCTION_TRACKING,),
        key_columns=("stat_date", "project_code", "stage_code"),
        notes="场景①落表链路终点：dwd_data_production_chain → dws_production_efficiency_daily → 本表",
    ),
    AdsProduct(
        ordinal=3,
        table="ads_badcase_root_cause_distribution",
        title_cn="Badcase 根因分布表",
        theme=AdsTheme.DATA_QUALITY_ASSET,
        serves=(BusinessPlatform.ISSUE_ANALYSIS, BusinessPlatform.EVALUATION),
        domain=DataDomain.EVALUATION,
        dimensions_cn="根因分类 / 子分类（× 统计日期 × 项目 × 模型版本）",
        core_metrics_cn=("数量", "占比", "趋势"),
        scenario_cn=(
            "模型 v3.2 评测产出 3,200 个 Badcase，分布显示感知漏检占 45%，"
            "其中夜间行人占 28% 且趋势上升 → 问题分析平台把结论推送挖掘平台，"
            "定向补采 2,000 条重训，该类漏检下降 60%（[S1-05] 第四章 1）"
        ),
        source_tables=("dws_badcase_statistics", "dwd_badcase_detail"),
        consumed_by=(ClosedLoopService.MODEL_ITERATION_EVALUATION,),
        key_columns=(
            "stat_date",
            "project_code",
            "model_version",
            "root_cause_category",
            "root_cause_sub_category",
        ),
        notes="表名域段写作 badcase_ 而非 evaluation_，源文实战示例即如此（见 naming.DOMAIN_ALIASES）",
    ),
    AdsProduct(
        ordinal=4,
        table="ads_scene_library_summary",
        title_cn="场景库汇总表",
        theme=AdsTheme.DATA_QUALITY_ASSET,
        serves=(BusinessPlatform.DATA_MINING, BusinessPlatform.DATA_MANAGEMENT),
        domain=DataDomain.DATASET,
        dimensions_cn="场景类型 × 场景标签",
        core_metrics_cn=("总量", "高质量量", "关联 Badcase", "覆盖度"),
        scenario_cn=(
            "场景库定义 1,200 个标签、覆盖度 82%；盘点发现「施工区域」仅 150 条"
            "（达标线 2,000 条）且 Badcase 逐月上升 → 生成场景缺口清单，"
            "定向补采两周后达标、状态流转 COVERED（[S1-05] 第四章 2）"
        ),
        source_tables=("dws_scene_distribution", "dwd_scene_gap_detail"),
        consumed_by=(
            ClosedLoopService.SCENE_SEARCH_CURATION,
            ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        ),
        key_columns=("stat_date", "scene_type", "scene_tag_id"),
        notes="缺口状态机 GAP → FILLING → COVERED 驱动定向补采",
    ),
    AdsProduct(
        ordinal=5,
        table="ads_hard_case_library",
        title_cn="难例库表",
        theme=AdsTheme.DATA_QUALITY_ASSET,
        serves=(BusinessPlatform.TRAINING, BusinessPlatform.DATA_MANAGEMENT),
        domain=DataDomain.EVALUATION,
        dimensions_cn="难例类别 × 来源 × 模型版本",
        core_metrics_cn=("数量", "采纳率", "闭环验证效果"),
        scenario_cn=(
            "v3.2 评测挖出 3,200 条难例（夜间行人 900、逆光车辆 700），"
            "训练平台采纳 2,800 条（采纳率 87.5%）混入 v3.3 训练集，"
            "重训后夜间行人漏检率下降 60%（[S1-05] 第四章 3）"
        ),
        source_tables=(
            "dwd_badcase_detail",
            "dwd_mining_result_detail",
            "dwd_vehicle_trigger_detail",
        ),
        consumed_by=(ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,),
        key_columns=("stat_date", "hard_case_category", "source_type", "model_version"),
        notes="场景④落表链路终点：ods_vehicle_trigger_event → dwd_vehicle_trigger_detail → 本表",
    ),
    AdsProduct(
        ordinal=6,
        table="ads_data_asset_catalog",
        title_cn="数据资产目录表",
        theme=AdsTheme.ASSET_OPERATION,
        serves=(BusinessPlatform.DATA_MANAGEMENT,),
        domain=DataDomain.DATASET,
        dimensions_cn="资产类型 × 负责人",
        core_metrics_cn=("注册数", "使用次数", "质量评分"),
        scenario_cn=(
            "「城区 NOA 主数据集 v12」本季度被 12 个训练任务引用、质量评分 4.6，列为核心资产；"
            "同期 3 个低分老旧数据集长期无人使用 → 标记归档释放存储，"
            "资产目录保持「新鲜可用」（[S1-05] 第六章表 6）"
        ),
        source_tables=("dws_dataset_statistics", "dwd_dataset_version_detail"),
        consumed_by=(ClosedLoopService.DATASET_VERSION_DELIVERY,),
        key_columns=("stat_date", "asset_type", "asset_id"),
        notes="登记数据集/场景库/难例库/评测集四类资产",
    ),
    AdsProduct(
        ordinal=7,
        table="ads_model_version_comparison",
        title_cn="模型版本对比表",
        theme=AdsTheme.MODEL_ITERATION_ROLLOUT,
        serves=(BusinessPlatform.EVALUATION, BusinessPlatform.TRAINING),
        domain=DataDomain.TRAINING,
        dimensions_cn="模型版本 × 数据集 × 场景类型",
        core_metrics_cn=("通过率", "Badcase 率", "平均指标分", "与基线对比"),
        scenario_cn=(
            "v3.3 与 v3.2 在「城区 NOA 评测集 v5」对比——总体通过率 91.2% vs 87.5%，"
            "夜间场景显著提升 +8.3pp（难例闭环见效）；但高速场景回归 -1.2pp →"
            "评测平台标记回归项，定位到车道线检测变更，修复后再 OTA，避免带病上车"
            "（[S1-05] 第五章表 7）"
        ),
        source_tables=("dws_evaluation_summary", "dws_training_efficiency_daily"),
        consumed_by=(ClosedLoopService.MODEL_ITERATION_EVALUATION,),
        key_columns=(
            "model_version",
            "baseline_model_version",
            "dataset_id",
            "dataset_version",
            "scene_type",
        ),
        notes="regression_flag 为真即「带病上车」拦截点，OTA 放行门在此表上判定",
    ),
    AdsProduct(
        ordinal=8,
        table="ads_ota_deployment_summary",
        title_cn="OTA 部署汇总表",
        theme=AdsTheme.MODEL_ITERATION_ROLLOUT,
        serves=(BusinessPlatform.MONITOR_DASHBOARD, BusinessPlatform.ISSUE_ANALYSIS),
        domain=DataDomain.DEPLOYMENT,
        dimensions_cn="统计日期 × 车型 × 软件版本",
        core_metrics_cn=("升级任务数/车辆数", "成功率", "灰度进度", "发布后回传触发量"),
        scenario_cn=(
            "v3.3 灰度推送 500 台车：升级成功率 99.2%，发布后一周回传触发量环比增长 30%"
            "（新版本主动采集策略生效），安全相关问题 0 起 → 确认全量推送，"
            "回传数据反哺下一轮训练（[S1-05] 第五章表 8）"
        ),
        source_tables=("dws_deployment_statistics", "dwd_ota_deployment_detail"),
        consumed_by=(ClosedLoopService.MODEL_ITERATION_EVALUATION,),
        key_columns=("ota_task_id",),
        date_column=None,
        notes=(
            "⚠️ 原文计算维度是「统计日期 × 车型 × 软件版本」，"
            "catalog 注册表把主键定为 ota_task_id（一次 OTA 任务一行），"
            "车型分布落在 vehicle_model_distribution（JSON）字段里。"
            "本服务层按注册表口径查询，车型维度经 JSON 字段展开"
        ),
    ),
    AdsProduct(
        ordinal=9,
        table="ads_trigger_heatmap",
        title_cn="触发事件热力图表",
        theme=AdsTheme.MODEL_ITERATION_ROLLOUT,
        serves=(BusinessPlatform.ISSUE_ANALYSIS, BusinessPlatform.DATA_MINING),
        domain=DataDomain.TRIGGER,
        dimensions_cn="统计日期 × 车型 × 触发类型 × 地理网格",
        core_metrics_cn=("触发总量", "上传/处理完成率", "入数据集量"),
        scenario_cn=(
            "热力图显示触发集中在城区晚高峰路口；某区域「AEB 误触发」周环比上升 45% →"
            "问题分析平台关联该区域回传数据定位到逆光路口场景，挖掘平台下发定向采集需求，"
            "补数重训后误触发率回落（[S1-05] 第五章表 9）"
        ),
        source_tables=(
            "dws_trigger_statistics",
            "dwd_vehicle_trigger_detail",
            "dwd_shadow_mode_detail",
        ),
        consumed_by=(ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,),
        key_columns=("stat_date", "project_code", "geo_grid_id", "trigger_type"),
        notes=(
            "⚠️ 原文维度含「车型」，catalog 注册表的主键用 project_code 代替车型段，"
            "车型下钻经 sample_data_id 回到 dwd_vehicle_trigger_detail"
        ),
    ),
    AdsProduct(
        ordinal=10,
        table="ads_storage_cost_dashboard",
        title_cn="存储成本看板表",
        theme=AdsTheme.COST_GOVERNANCE,
        serves=(BusinessPlatform.MONITOR_DASHBOARD, BusinessPlatform.DATA_MANAGEMENT),
        domain=DataDomain.CLOSED_LOOP,
        dimensions_cn="存储介质 × 生命周期分层 × 数据类型",
        core_metrics_cn=("容量", "成本", "治理动作量", "节省额"),
        scenario_cn=(
            "月末复盘：一份 240GB 的雨夜城区采集数据若常年驻留 NAS + OSS 标准年成本约 ¥3,226，"
            "走元信息驱动的治理流程（预热上 NAS 仅占 9 天 → 淘汰回温层 → 30 天无访问降冷 →"
            "90 天无访问归档）年成本压到 ¥203，降幅约 94%；成本环比增长超 10% 自动告警"
            "（[S1-全景] 第八章③）"
        ),
        source_tables=("dws_closed_loop_storage_cost_daily", "dwd_closed_loop_storage_lifecycle"),
        consumed_by=(ClosedLoopService.DATASET_VERSION_DELIVERY,),
        key_columns=("stat_date", "storage_media", "lifecycle_stage", "data_type"),
        notes=(
            "⚠️ 原文的六项闭环业务服务（[S1-全景] 第九章）没有「成本治理」这一项，"
            "本项目把成本看板挂在「📦 数据集版本与交付」服务下，"
            "理由是资产交付与存储账单同属资产运营视角——这是本项目的归类，不是原文方案"
        ),
    ),
    AdsProduct(
        ordinal=11,
        table="ads_mining_tag_dashboard",
        title_cn="挖掘标签分布看板表",
        theme=AdsTheme.MINING_OPERATION,
        serves=(BusinessPlatform.MONITOR_DASHBOARD, BusinessPlatform.DATA_MANAGEMENT),
        domain=DataDomain.MINING,
        dimensions_cn="统计日期 × 项目 × 标签（三来源：采集/规则/模型）",
        core_metrics_cn=(
            "三来源标签量构成",
            "clip 覆盖率",
            "向量化率",
            "检索热度",
            "候选池待审量",
        ),
        scenario_cn=(
            "周会看盘：盯住标签资产的健康度——三来源（采集/规则/模型）标签量构成、"
            "clip 覆盖率、向量化率、检索热度与候选池待审量（[S1-05] 第六章表 11）"
        ),
        source_tables=("dws_mining_tag_coverage_daily", "dws_mining_efficiency_daily"),
        consumed_by=(ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,),
        key_columns=("stat_date", "project_code", "tag_id"),
        notes=(
            "⚠️ 原文的「向量化率、检索热度」两项指标在 catalog 注册表里没有对应字段"
            "（向量与检索指标落在 dws_mining_efficiency_daily 的 embedding_image_count / "
            "vector_search_p95_ms），本服务层在需要时从该 DWS 表补齐，向量检索本身属 vector 子系统"
        ),
    ),
)

#: 11 张 ADS 表的表名集合——查询层的表白名单。
ADS_TABLE_NAMES: Final[tuple[str, ...]] = tuple(p.table for p in PRODUCTS)

_BY_TABLE: Final[dict[str, AdsProduct]] = {p.table: p for p in PRODUCTS}


def get_product(table: str) -> AdsProduct:
    """按表名取数据产品定义。

    Args:
        table: ADS 表名，如 ``ads_closed_loop_dashboard``。

    Returns:
        对应的 :class:`AdsProduct`。

    Raises:
        UnknownTableError: 表不在 11 张产品矩阵内。
    """
    try:
        return _BY_TABLE[table]
    except KeyError:
        raise UnknownTableError(
            f"{table!r} 不在 ADS 数据产品矩阵（{len(PRODUCTS)} 张）内；"
            f"可选：{', '.join(ADS_TABLE_NAMES)}"
        ) from None


def products_by_theme(theme: AdsTheme) -> tuple[AdsProduct, ...]:
    """取某个业务主题下的全部数据产品。"""
    return tuple(p for p in PRODUCTS if p.theme is theme)


def products_by_platform(platform: BusinessPlatform) -> tuple[AdsProduct, ...]:
    """取某个业务平台消费的全部数据产品（[S1-05] 第二章「主要服务对象」列）。"""
    return tuple(p for p in PRODUCTS if platform in p.serves)


def products_by_service(service: ClosedLoopService) -> tuple[AdsProduct, ...]:
    """取某项闭环业务服务读取的全部数据产品。"""
    return tuple(p for p in PRODUCTS if service in p.consumed_by)


def matrix_rows() -> list[dict[str, str]]:
    """产品矩阵的表格化视图，供文档生成与运维自检使用。"""
    return [
        {
            "序": str(p.ordinal),
            "表名": p.table,
            "中文名": p.title_cn,
            "业务主题": p.theme.name_cn,
            "主要服务对象": p.serves_cn,
            "计算维度": p.dimensions_cn,
            "闭环业务服务": " / ".join(s.name_cn for s in p.consumed_by),
        }
        for p in PRODUCTS
    ]


def render_matrix() -> str:
    """把产品矩阵渲染成 Markdown 表格（运维 `python -m` 直接打印）。"""
    rows = matrix_rows()
    headers = list(rows[0])
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(r[h] for h in headers) + " |" for r in rows)
    return "\n".join(lines)
