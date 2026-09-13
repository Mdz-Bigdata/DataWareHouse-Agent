"""规则挖掘引擎 + VLM 推理挖掘：三级漏斗的前两层。

来源
----
· [S3-04] 系列三 · 数据挖掘与 AI 第 4 篇《数据闭环规则挖掘引擎实战：从结构化元数据中
  批量发现高价值场景》（小周，2026-09-12）——本子系统主线；
· [S3-03] 同系列第 3 篇《数据闭环统一标签体系设计》（2026-09-11）——双输出往哪落、
  caption 怎么归属、未审核标签为什么进不了训练集；
· [S3-02] 同系列第 2 篇《分层抽帧策略》——推理抽帧「每 clip 打分选 1~5 关键帧」；
· [S3-01] 同系列第 1 篇《数据挖掘平台架构设计》——控制面/数据面、成本分级、
  OpenAPI 出口、11 张新增表。
· ⚠️ [S3-05]《VLM 推理挖掘》**本项目未取得原文**，只有 [S3-04] 结尾的一句预告
  （「选帧打分、双输出（标签 + caption）、Ray + GPU 调度与断点续跑」），
  所以 vlm.py 里凡是那四篇没写的参数一律标「⚠️ 原文未明确，本项目设计」。

一句话方案
----------
**规则即数据**：规则配置存控制面 MySQL，经 Flink CDC 入湖（ods_mining_rule_config）；
六大种类的规则按条件来源分流到 T+1 批（Spark SQL on Paimon）与准实时（Flink）两条腿；
命中一律经统一标签服务打标（携带 rule_id 血缘），同时写 dwd_mining_result_detail；
规则写不出来的语义级长尾场景（「施工区锥桶摆放混乱」「行人撑着花伞」）交给 VLM 推理补。

模块地图
--------
:mod:`.constants`    原文出现的每个数字与专名，逐字登记并标出处
:mod:`.rules`        声明式规则模型：六大种类、双模式表达、四态生命周期、
                     以及信号条件的**求值**（:func:`sustained_matches`）
:mod:`.compiler`     规则 → SQL：批走增量水位 SELECT，流走事件窗口 / MATCH_RECOGNIZE
:mod:`.executor`     批流双模执行 + 执行追溯（dwd_mining_task_detail）+ 补抽帧联动
:mod:`.watermark`    增量扫描水位（存控制面，不占湖仓）
:mod:`.scoring`      高价值评分与排序（⚠️ 公式全部为本项目设计）
:mod:`.gaps`         场景缺口识别（dwd_scene_gap_detail）与定向采集需求
:mod:`.config_sync`  规则配置的 CDC 入湖与回读
:mod:`.vlm`          VLM 推理挖掘：选帧 → 批次 → 断点续跑 → 双输出 → 标签服务
:mod:`.backends`     外部依赖适配（SQL 后端 / 统一标签服务 / 补抽帧派发）
:mod:`.tables`       表引用与列契约（全部派生自 catalog.registry，本模块不手写列名）

原文写死的数字（改一个就不是原文的规则了）
------------------------------------------
======================================  ===========================================
CAN 减速度 < **-4** m/s² 持续 ≥ **0.5** s  :data:`~.constants.HARSH_DECEL_THRESHOLD_MPS2`
                                        / :data:`~.constants.HARSH_DECEL_MIN_DURATION_SEC`
事件窗口 前 **15** 秒 / 后 **5** 秒       :data:`~.constants.EVENT_WINDOW_BEFORE_SEC`
                                        / :data:`~.constants.EVENT_WINDOW_AFTER_SEC`
**亿级**以下 **4** 小时内跑完            :data:`~.constants.BATCH_SCAN_ROW_CEILING`
                                        / :data:`~.constants.BATCH_SLA_HOURS`
**六**大规则种类                         :data:`~.constants.RULE_TYPE_COUNT`
每 clip 选 **1~5** 关键帧                :data:`~.constants.INFERENCE_MIN_KEYFRAMES`
                                        / :data:`~.constants.INFERENCE_MAX_KEYFRAMES`
新增 **11** 张表（1/8/1/1）              :data:`~.constants.MINING_TABLE_COUNT`
======================================  ===========================================

外部编排的入口
--------------
本包不自行调度：控制面按 [S3-01] 四的「控制面/数据面分离」下发任务，
数据面这边的公开入口就是下面几个——:class:`BatchRuleExecutor`（T+1 批）、
:class:`StreamRuleExecutor`（准实时流 + CAN 采样求值）、:class:`VlmInferenceEngine`
（GPU 推理）、:class:`SceneGapDetector`（缺口识别）、:class:`RuleConfigLoader`（读规则）。
对外 HTTP 出口的路径见 :func:`rule_job_endpoint` / :func:`rule_job_progress_endpoint`
/ :func:`tag_coverage_endpoint`。
"""

from __future__ import annotations

from .backends import (
    BackendError,
    BackfillRequest,
    DryRunBackend,
    FrameBackfillDispatcher,
    InMemoryResultSink,
    InMemoryTagService,
    LakehouseBackfillDispatcher,
    ResultSink,
    SqlBackend,
    SqlResultSink,
    TagService,
    TagWriteRequest,
    rule_job_endpoint,
    rule_job_progress_endpoint,
)
from .compiler import (
    CompiledQuery,
    CompileError,
    RuleCompiler,
    ScanPlan,
    default_batch_plan,
    frame_batch_plan,
    harsh_decel_reference_sql,
)
from .config_sync import (
    RuleConfigCdcJob,
    RuleConfigLoader,
    render_cdc_source_ddl,
    render_cdc_sync_job,
    render_mysql_control_plane_ddl,
    rows_to_rules,
    rule_from_row,
)
from .constants import (
    BATCH_SCAN_ROW_CEILING,
    BATCH_SLA_HOURS,
    BATCH_SLA_SECONDS,
    EVENT_WINDOW_AFTER_SEC,
    EVENT_WINDOW_BEFORE_SEC,
    EVENT_WINDOW_TOTAL_SEC,
    FUNNEL_STAGES,
    HARSH_DECEL_MIN_DURATION_SEC,
    HARSH_DECEL_THRESHOLD_MPS2,
    HIGH_VALUE_SOURCES,
    INFERENCE_MAX_KEYFRAMES,
    INFERENCE_MIN_KEYFRAMES,
    MINING_TABLE_COUNT,
    MINING_TABLE_COUNT_BY_LAYER,
    RULE_TYPE_COUNT,
    VLM_CAPTION_TAG_CATEGORY,
    VLM_LONG_TAIL_EXAMPLES,
    VLM_OUTPUT_KINDS,
    VLM_TAG_SOURCE,
)
from .executor import (
    BatchRuleExecutor,
    ExecutionReport,
    RuleRunRecord,
    StreamRuleExecutor,
    TaskStatus,
    task_insert_sql,
)
from .gaps import (
    CollectDemand,
    GapStatus,
    SceneGap,
    SceneGapDetector,
    coverage_sql,
    evaluate_gap,
    gap_insert_sql,
    gap_severity,
    tag_coverage_endpoint,
)
from .rules import (
    SAMPLE_RULES,
    Condition,
    ConditionGroup,
    Dialect,
    EventCondition,
    ExecutionMode,
    ExpressionMode,
    GeoFenceCondition,
    ModelOutputCondition,
    RawSqlCondition,
    RuleChange,
    RuleDefinition,
    RulePriority,
    RuleStatus,
    RuleType,
    RuleValidationError,
    SignalCondition,
    SignalSample,
    SustainedHit,
    TagCondition,
    TimeWindowCondition,
    VectorizePolicy,
    condition_from_dict,
    harsh_deceleration_condition,
    rules_by_mode,
    sort_by_priority,
    sustained_matches,
)
from .scoring import (
    DEFAULT_WEIGHTS,
    TIER_THRESHOLDS,
    ScoreBreakdown,
    ScoreWeights,
    ValueTier,
    classify_tier,
    rank_hits,
    resolve_vectorize_policy,
    score_hit,
    top_n,
)
from .tables import (
    ALL_REFS,
    MINING_NEW_TABLES,
    VEHICLE_SIGNAL_STREAM,
    StreamRef,
    TableRef,
)
from .vlm import (
    CaptionVectorSink,
    CheckpointStore,
    EchoVlmClient,
    ImageTagWriteRequest,
    InferBatch,
    InferCandidate,
    InMemoryCheckpointStore,
    InMemoryModelTagSink,
    JsonFileCheckpointStore,
    ModelTagSink,
    VlmClient,
    VlmInferenceEngine,
    VlmInferError,
    VlmInferReport,
    VlmOutput,
    VlmOutputError,
    VlmRunRecord,
    VlmTag,
    build_prompt,
    partition_sendable,
    plan_batches,
)
from .watermark import (
    InMemoryWatermarkStore,
    JsonFileWatermarkStore,
    Watermark,
    WatermarkStore,
    incremental_predicate,
    watermark_column,
)

__all__ = [
    # 规则模型
    "RuleDefinition",
    "RuleType",
    "RuleStatus",
    "RulePriority",
    "RuleChange",
    "RuleValidationError",
    "ExecutionMode",
    "ExpressionMode",
    "Dialect",
    "VectorizePolicy",
    "Condition",
    "ConditionGroup",
    "TagCondition",
    "GeoFenceCondition",
    "TimeWindowCondition",
    "SignalCondition",
    "ModelOutputCondition",
    "EventCondition",
    "RawSqlCondition",
    "condition_from_dict",
    "rules_by_mode",
    "sort_by_priority",
    "SAMPLE_RULES",
    # 原文那条带数字的规则：声明 + 求值
    "harsh_deceleration_condition",
    "SignalSample",
    "SustainedHit",
    "sustained_matches",
    # 编译
    "RuleCompiler",
    "CompiledQuery",
    "CompileError",
    "ScanPlan",
    "default_batch_plan",
    "frame_batch_plan",
    "harsh_decel_reference_sql",
    # 执行与追溯
    "BatchRuleExecutor",
    "StreamRuleExecutor",
    "RuleRunRecord",
    "ExecutionReport",
    "TaskStatus",
    "task_insert_sql",
    # 水位
    "Watermark",
    "WatermarkStore",
    "InMemoryWatermarkStore",
    "JsonFileWatermarkStore",
    "watermark_column",
    "incremental_predicate",
    # 评分与排序
    "ScoreWeights",
    "DEFAULT_WEIGHTS",
    "ScoreBreakdown",
    "ValueTier",
    "TIER_THRESHOLDS",
    "score_hit",
    "classify_tier",
    "rank_hits",
    "top_n",
    "resolve_vectorize_policy",
    # 场景缺口
    "SceneGap",
    "SceneGapDetector",
    "GapStatus",
    "CollectDemand",
    "evaluate_gap",
    "gap_severity",
    "coverage_sql",
    "gap_insert_sql",
    "tag_coverage_endpoint",
    # 规则即数据（CDC）
    "RuleConfigLoader",
    "RuleConfigCdcJob",
    "rule_from_row",
    "rows_to_rules",
    "render_mysql_control_plane_ddl",
    "render_cdc_source_ddl",
    "render_cdc_sync_job",
    # VLM 推理挖掘
    "VlmInferenceEngine",
    "VlmClient",
    "EchoVlmClient",
    "VlmOutput",
    "VlmTag",
    "VlmInferError",
    "VlmOutputError",
    "InferCandidate",
    "InferBatch",
    "plan_batches",
    "partition_sendable",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "JsonFileCheckpointStore",
    "ModelTagSink",
    "InMemoryModelTagSink",
    "ImageTagWriteRequest",
    "CaptionVectorSink",
    "VlmRunRecord",
    "VlmInferReport",
    "build_prompt",
    # 外部依赖适配
    "SqlBackend",
    "DryRunBackend",
    "BackendError",
    "TagService",
    "InMemoryTagService",
    "TagWriteRequest",
    "ResultSink",
    "InMemoryResultSink",
    "SqlResultSink",
    "FrameBackfillDispatcher",
    "LakehouseBackfillDispatcher",
    "BackfillRequest",
    "rule_job_endpoint",
    "rule_job_progress_endpoint",
    # 表引用
    "TableRef",
    "StreamRef",
    "VEHICLE_SIGNAL_STREAM",
    "ALL_REFS",
    "MINING_NEW_TABLES",
    # 原文关键数字（便捷再导出）
    "HARSH_DECEL_THRESHOLD_MPS2",
    "HARSH_DECEL_MIN_DURATION_SEC",
    "EVENT_WINDOW_BEFORE_SEC",
    "EVENT_WINDOW_AFTER_SEC",
    "EVENT_WINDOW_TOTAL_SEC",
    "BATCH_SCAN_ROW_CEILING",
    "BATCH_SLA_HOURS",
    "BATCH_SLA_SECONDS",
    "RULE_TYPE_COUNT",
    "FUNNEL_STAGES",
    "HIGH_VALUE_SOURCES",
    "MINING_TABLE_COUNT",
    "MINING_TABLE_COUNT_BY_LAYER",
    "INFERENCE_MIN_KEYFRAMES",
    "INFERENCE_MAX_KEYFRAMES",
    "VLM_OUTPUT_KINDS",
    "VLM_CAPTION_TAG_CATEGORY",
    "VLM_TAG_SOURCE",
    "VLM_LONG_TAIL_EXAMPLES",
]
