"""数据面：实际计算与主数据的所在地。

子系统「控制面/数据面分离」的数据面一半。控制面一半在
:mod:`adas_lakehouse.controlplane`，两面的边界契约也定义在那里
（:mod:`adas_lakehouse.controlplane.contracts`）。

来源：系列三「数据挖掘与 AI」第 1 篇《数据闭环数据挖掘平台架构设计：控制面/数据面
分离的工程实践》（2026-09-09，https://mp.weixin.qq.com/s/bSekzM_WjtGtAd_1WdbBAQ）。

原文第四章对数据面的定义只有一句：

  「数据面（湖仓）：clip / 图片 / 标签 / 向量全部在 Paimon，StarRocks 只做查询与向量
    索引加速，同样不持有主数据。」

注意最后半句——**StarRocks 也不持有主数据**。它里面的内表是 ADS 物化的副本，
掉了重新物化即可；Paimon 才是唯一事实源。

四个模块：

==================  ====================================================
:mod:`.assets`      资产清单：哪张表装哪类主数据、哪张表是复用不是新建、
                    挖掘域 11 张表的分层预算、image_id 内嵌 data_id 的
                    生成与还原
:mod:`.engines`     四个核心引擎（抽帧 / 规则挖掘 / VLM 推理 / Embedding）、
                    K8s 三区部署规格（4C8G × 2 起）、四个技术选型取舍
:mod:`.gpu`         GPU 池：VLM 推理与 Embedding 共享同池、分时错峰
                    （Embedding 凌晨 6 点前完成）、优先级队列与峰值抢占
                    （被抢者原序回队 + 留痕）、弹性扩缩、向量化成本分级
:mod:`.ray_engine`  Ray + vLLM 推理引擎：任务编排（切批次）+ 断点续跑
                    （按批落 checkpoint）——原文放弃 Triton 换来的正是这两样，
                    所以它们必须真的实现
:mod:`.query`       双路查询出口：检索明细与向量走 External Catalog 查
                    Paimon；看板经 ADS 物化至 StarRocks 内表毫秒级直查；
                    外表 HNSW 不可用时的内表降级路径
:mod:`.execution`   执行器：接信封、跑引擎、写湖仓、登记血缘、回指针；
                    以及给 mining/sampling/tags 复用的适配器骨架
==================  ====================================================

快速上手——实现一个子系统适配器::

    from adas_lakehouse.controlplane import TaskKind
    from adas_lakehouse.dataplane import BaseSubsystemAdapter, ExecutionResult

    class SamplingAdapter(BaseSubsystemAdapter):
        name = "sampling"
        kinds = frozenset({TaskKind.FRAME_SAMPLING})

        def run(self, ctx):
            # 自己连湖仓按 ctx.envelope.input_selector 取数、抽帧、产出行
            return [ExecutionResult(target_table="dwd_mining_image_frame_detail",
                                    rows=[...], data_id="COLLECT_BP_20240115143022_ab12")]

    def build_plane_adapter():
        return SamplingAdapter()

外部客户端（pymysql / neo4j / requests）全部延迟 import——本包可以在什么都没装的
环境里被 import，验收阶段的全量 import 检查不会因此失败。
"""

from __future__ import annotations

from .assets import (
    DATA_PLANE_TABLES,
    REUSED_TABLES,
    DataPlaneTable,
    LakehouseOwnershipError,
    alignment_principles,
    assert_lakehouse_owned,
    asset_inventory,
    data_id_of_image,
    image_id_for,
    mining_table_budget,
)
from .engines import (
    DEPLOY_ZONES,
    ENGINE_BY_KIND,
    ENGINES,
    TECH_TRADEOFFS,
    DeployZone,
    EngineSpec,
    ZoneSpec,
    engine_decoupling_note,
    engine_for,
)
from .execution import (
    BaseSubsystemAdapter,
    DataPlane,
    DryRunLakeSink,
    ExecutionContext,
    ExecutionResult,
    GpuAdmission,
    LakeSink,
    LineageSink,
    Neo4jLineageSink,
    NullLineageSink,
)
from .gpu import (
    DAY_WINDOW_START_HOUR,
    DEFAULT_PREEMPT_PRIORITY_GAP,
    DEFAULT_SLOTS_PER_GPU,
    EMBEDDING_WINDOW_DEADLINE_HOUR,
    GPU_POOL_SCALING_POLICY,
    HIGH_VALUE_SOURCES,
    NIGHT_WINDOW_START_HOUR,
    ORDINARY_DATA_SAMPLE_RATIO,
    PEAK_HOURS,
    EvictReason,
    GpuLease,
    GpuPool,
    GpuWindow,
    PreemptionRecord,
    ScaleOutcome,
    ValueTier,
    batch_quota,
    can_preempt,
    classify_value_tier,
    current_window,
    describe_gpu_policy,
    embedding_deadline_ok,
    fits_window,
    is_peak,
    vectorization_quota,
)
from .query import (
    VECTOR_INDEX_TYPE,
    QueryIntent,
    QueryPlan,
    QueryRoute,
    StarRocksClient,
    plan_query,
    route_for,
)
from .ray_engine import (
    DEFAULT_INFERENCE_BATCH_SIZE,
    RAY_CAPABILITIES_REQUIRED,
    TRITON_REJECTION_REASON,
    BatchResult,
    BatchRunner,
    CheckpointStore,
    DispatchResult,
    EchoBatchRunner,
    InferenceBatch,
    InMemoryCheckpointStore,
    RayClusterSpec,
    RayInferenceEngine,
    RayVllmBatchRunner,
    VllmEngineConfig,
    describe_engine_choice,
)

__all__ = [
    # 资产
    "DataPlaneTable",
    "DATA_PLANE_TABLES",
    "REUSED_TABLES",
    "LakehouseOwnershipError",
    "assert_lakehouse_owned",
    "mining_table_budget",
    "image_id_for",
    "data_id_of_image",
    "alignment_principles",
    "asset_inventory",
    # 引擎与部署
    "EngineSpec",
    "ENGINES",
    "ENGINE_BY_KIND",
    "engine_for",
    "engine_decoupling_note",
    "DeployZone",
    "ZoneSpec",
    "DEPLOY_ZONES",
    "TECH_TRADEOFFS",
    # GPU
    "GpuPool",
    "GpuLease",
    "GpuWindow",
    "ValueTier",
    "EvictReason",
    "PreemptionRecord",
    "ScaleOutcome",
    "current_window",
    "fits_window",
    "is_peak",
    "can_preempt",
    "embedding_deadline_ok",
    "classify_value_tier",
    "vectorization_quota",
    "batch_quota",
    "describe_gpu_policy",
    "EMBEDDING_WINDOW_DEADLINE_HOUR",
    "NIGHT_WINDOW_START_HOUR",
    "DAY_WINDOW_START_HOUR",
    "ORDINARY_DATA_SAMPLE_RATIO",
    "HIGH_VALUE_SOURCES",
    "PEAK_HOURS",
    "DEFAULT_SLOTS_PER_GPU",
    "DEFAULT_PREEMPT_PRIORITY_GAP",
    "GPU_POOL_SCALING_POLICY",
    # Ray + vLLM 推理引擎
    "RayInferenceEngine",
    "VllmEngineConfig",
    "RayClusterSpec",
    "InferenceBatch",
    "BatchResult",
    "BatchRunner",
    "EchoBatchRunner",
    "RayVllmBatchRunner",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "DispatchResult",
    "describe_engine_choice",
    "TRITON_REJECTION_REASON",
    "RAY_CAPABILITIES_REQUIRED",
    "DEFAULT_INFERENCE_BATCH_SIZE",
    # 查询
    "QueryIntent",
    "QueryRoute",
    "QueryPlan",
    "plan_query",
    "route_for",
    "StarRocksClient",
    "VECTOR_INDEX_TYPE",
    # 执行
    "DataPlane",
    "ExecutionContext",
    "ExecutionResult",
    "GpuAdmission",
    "BaseSubsystemAdapter",
    "LakeSink",
    "DryRunLakeSink",
    "LineageSink",
    "NullLineageSink",
    "Neo4jLineageSink",
]
