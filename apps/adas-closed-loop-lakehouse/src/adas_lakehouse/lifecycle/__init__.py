"""存储生命周期五级分层：PB 级数据成本治理。

来源：系列一第 7 篇《存储生命周期五级分层：智驾 PB 级数据成本治理实战》
（公众号「小周」2026-08-30，本仓库快照 a14.md）。

数据闭环是一个「只进不出」的增长系统——采集、量产回传、产线中间副本、训练
Checkpoint、数据集版本、仿真突发数据六大增长源叠加，存储体量按月近线性增长。
不治理的代价是三重失控：高价介质被滥用（NAS 单价约为 OSS 标准存储的 8–10 倍）、
冷数据占据热层（超过 80% 的历史数据几乎不再被访问）、成本指数级增长。
治理目标是让**存储成本增速只有数据增速的约 1/20**。

模块地图
--------

==============  ================================================================
模块             职责
==============  ================================================================
``tiers``        五级分层模型（H1 热 / H2 温 / C1 冷 / C2 归档 / D 删除）、介质、单价
``policy``       TTL 保留期表、NAS 四条淘汰纪律、血缘保护、四道安全闸与五步闭环的定义
``records``      两张表的行对象：LifecycleRecord / CostDailyRow
``decision``     扫描决策引擎：快照 → 动作，删除三重确认与 checksum 闸在此把关
``cost``         成本模型、案例重放（核对 94% 口径）、月末复盘与两条预算告警线
``tables``       两张表的 TableSpec 契约与全湖注册表对账
``repository``   Paimon（Flink SQL Gateway）/ StarRocks / 内存三种仓储
``scheduler``    五步日级调度闭环编排
==============  ================================================================

三十秒上手::

    from datetime import datetime
    from adas_lakehouse.lifecycle import (
        GovernanceRun, InMemoryRepository, LifecycleRecord, TrainingContext,
    )

    repo = InMemoryRepository([
        LifecycleRecord(
            data_id="COLLECT_BP_20260301123045_b7e2",
            file_path="s3://adas-raw/collect/2026/03/01/clip.mp4",
            data_type="raw", file_size_bytes=240 * 1024 ** 3,
            create_time=datetime(2026, 3, 1), source_domain="collect",
        )
    ])
    run = GovernanceRun(repo)
    summary = run.run_daily(training=TrainingContext(now=datetime(2026, 7, 29)))

核对原文的 94% 降本口径::

    from adas_lakehouse.lifecycle import replay_case_study, reconcile_source_figures
    replay_case_study()["reduction_rate_pct"]   # '93.71%'，原文取整写「约 94%」
    reconcile_source_figures()                  # 五组数字的逐项对账结论

外部依赖（Flink / StarRocks 客户端）一律延迟 import，``import`` 本包在任何环境下都不会炸。
"""

from __future__ import annotations

from .cost import (
    ALERT_COST_MOM_GROWTH,
    ALERT_NAS_USAGE_RATIO,
    CASE_COST_BOOK,
    CASE_DATA_ID,
    CASE_SIZE_GB,
    CASE_TIMELINE,
    COST_VS_DATA_GROWTH_RATIO,
    SOURCE_MONTHLY_REVIEW,
    SOURCE_REDUCTION_RATE,
    CaseSnapshot,
    CostModel,
    MonthlyReview,
    aggregate_cost_daily,
    budget_alerts,
    dashboard_metrics,
    reconcile_source_figures,
    replay_case_study,
)
from .decision import (
    ActionType,
    Decision,
    NasContext,
    TrainingContext,
    checksum_gate,
    decide,
    delete_triple_confirm,
    evict_status_after,
    lru_evict_order,
    scan,
)
from .externalize import (
    EXTERNALIZED_META_FIELDS,
    FLOW_CYCLE,
    GROWTH_SOURCES,
    LAKEHOUSE_METADATA_CONTENT,
    LOSS_OF_CONTROL,
    MEDIA_ROLES,
    REUSABLE_LESSONS,
    MediaRole,
    check_externalized,
    check_flow_direction,
    check_source_of_truth,
    externalization_leverage,
)
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
    PIPELINE_STEPS,
    RETENTION_SCHEDULE,
    SAFETY_GATES,
    TIER_MODEL_THRESHOLDS,
    DataType,
    EvictScenario,
    EvictStatus,
    ExpirePolicy,
    RetentionRule,
    expire_policy_for,
    media_for_stage,
    retention_for,
    target_stage_by_age,
)
from .records import (
    COST_DAILY_COLUMNS,
    LIFECYCLE_COLUMNS,
    CostDailyRow,
    LifecycleRecord,
)
from .repository import (
    FlinkSqlGatewayClient,
    InMemoryRepository,
    LifecycleRepository,
    PaimonRepository,
    StarRocksRepository,
)
from .scheduler import (
    EXECUTE_RATE_LIMIT_PER_SEC,
    LARGE_BATCH_FILE_THRESHOLD,
    LARGE_BATCH_TB_THRESHOLD,
    ExecutionResult,
    GovernancePlan,
    GovernanceRun,
    NoopExecutor,
    RehearsalReport,
    StorageExecutor,
    preheat_hit_rate,
)
from .tables import (
    COST_DAILY_TABLE,
    LIFECYCLE_TABLE,
    cost_daily_table_spec,
    lifecycle_table_spec,
    reconcile_with_registry,
    validate_specs,
)
from .tiers import (
    ARCHIVE_RESTORE_SLA_HOURS,
    ARCHIVE_VS_HOT_PRICE_DIVISOR,
    COLD_HISTORY_SHARE,
    FILE_STORAGE_CLASS_VALUES,
    GOVERNANCE_PRINCIPLES,
    HOT_TIER_MEDIA_LABEL,
    MEDIA_OF_STAGE,
    MEDIA_OF_STORAGE_CLASS,
    NAS_RELATIVE_PRICE_RANGE,
    RELATIVE_PRICE,
    SAMPLE_PRICE_YUAN_PER_GB_MONTH,
    STAGE_OF_MEDIA,
    STORAGE_CLASS_OF_MEDIA,
    TIERS,
    LifecycleStage,
    StorageMedia,
    TierDefinition,
    colder_than,
    media_of_storage_class,
    one_tier_colder,
    one_tier_warmer,
    storage_class_of,
    tier_of_stage,
    verify_price_consistency,
    verify_storage_class_bridge,
)

__all__ = [
    # ---- 五级分层模型 ----
    "StorageMedia",
    "LifecycleStage",
    "TierDefinition",
    "TIERS",
    "tier_of_stage",
    "MEDIA_OF_STAGE",
    "STAGE_OF_MEDIA",
    "colder_than",
    "one_tier_warmer",
    "one_tier_colder",
    "RELATIVE_PRICE",
    "NAS_RELATIVE_PRICE_RANGE",
    "SAMPLE_PRICE_YUAN_PER_GB_MONTH",
    "COLD_HISTORY_SHARE",
    "ARCHIVE_VS_HOT_PRICE_DIVISOR",
    "ARCHIVE_RESTORE_SLA_HOURS",
    "GOVERNANCE_PRINCIPLES",
    "HOT_TIER_MEDIA_LABEL",
    "verify_price_consistency",
    # ---- 文件域 storage_class 桥 ----
    "FILE_STORAGE_CLASS_VALUES",
    "STORAGE_CLASS_OF_MEDIA",
    "MEDIA_OF_STORAGE_CLASS",
    "storage_class_of",
    "media_of_storage_class",
    "verify_storage_class_bridge",
    # ---- PB 级大文件外置 ----
    "MediaRole",
    "MEDIA_ROLES",
    "FLOW_CYCLE",
    "LAKEHOUSE_METADATA_CONTENT",
    "EXTERNALIZED_META_FIELDS",
    "GROWTH_SOURCES",
    "LOSS_OF_CONTROL",
    "REUSABLE_LESSONS",
    "check_externalized",
    "check_source_of_truth",
    "check_flow_direction",
    "externalization_leverage",
    # ---- 规则与 TTL ----
    "DataType",
    "ExpirePolicy",
    "EvictStatus",
    "EvictScenario",
    "RetentionRule",
    "RETENTION_SCHEDULE",
    "TIER_MODEL_THRESHOLDS",
    "EVICT_RULES",
    "LINEAGE_BUMP_TIERS",
    "LINEAGE_BUMP_GRACE_DAYS",
    "LINEAGE_BUMP_FLOOR_STAGE",
    "COLD_TO_ARCHIVE_NO_ACCESS_DAYS",
    "DEEP_ARCHIVE_NO_ACCESS_DAYS",
    "NAS_TRAINING_DONE_BUFFER_DAYS",
    "NAS_CHECKPOINT_KEEP_VERSIONS",
    "NAS_WATERMARK_USAGE",
    "NAS_LRU_NO_ACCESS_DAYS",
    "DELETE_TRIPLE_CONFIRM",
    "SAFETY_GATES",
    "PIPELINE_STEPS",
    "retention_for",
    "expire_policy_for",
    "target_stage_by_age",
    "media_for_stage",
    # ---- 行对象 ----
    "LifecycleRecord",
    "CostDailyRow",
    "LIFECYCLE_COLUMNS",
    "COST_DAILY_COLUMNS",
    # ---- 决策 ----
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
    # ---- 成本 ----
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
    "COST_VS_DATA_GROWTH_RATIO",
    "ALERT_COST_MOM_GROWTH",
    "ALERT_NAS_USAGE_RATIO",
    "budget_alerts",
    "aggregate_cost_daily",
    "dashboard_metrics",
    "reconcile_source_figures",
    # ---- 表契约 ----
    "LIFECYCLE_TABLE",
    "COST_DAILY_TABLE",
    "lifecycle_table_spec",
    "cost_daily_table_spec",
    "validate_specs",
    "reconcile_with_registry",
    # ---- 仓储 ----
    "LifecycleRepository",
    "InMemoryRepository",
    "PaimonRepository",
    "StarRocksRepository",
    "FlinkSqlGatewayClient",
    # ---- 调度 ----
    "GovernanceRun",
    "GovernancePlan",
    "RehearsalReport",
    "ExecutionResult",
    "StorageExecutor",
    "NoopExecutor",
    "preheat_hit_rate",
    "LARGE_BATCH_FILE_THRESHOLD",
    "LARGE_BATCH_TB_THRESHOLD",
    "EXECUTE_RATE_LIMIT_PER_SEC",
]
