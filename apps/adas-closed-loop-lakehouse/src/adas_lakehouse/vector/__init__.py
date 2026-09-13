"""HNSW 向量索引落地 StarRocks：从 Paimon 外部表到语义检索。

来源
----
· 系列二 · 湖仓实战 第 7 篇《HNSW 向量索引落地 StarRocks：从 Paimon 外部表到语义检索》
  （小周，2026-09-06）——本子系统的主线设计；
· 《Paimon 2.0 系列：从 JSON 到 Variant，电商与智驾的半结构化实践》（李劲松，2026-08-17）
  ——向量元数据列 vector_meta 的半结构化落地依据（见 variant 模块）。

一句话方案
----------
不新增一套向量数据库：**向量落湖于 Paimon，索引建在 StarRocks 外部表上**。
Paimon 保持单一事实源，StarRocks 只提供检索加速、不持有主数据。

模块地图
--------
====================  ==========================================================
params                所有常量的唯一出处：P95 ≤ 2 秒、凌晨 6 点前、五步 / 四类 /
                      五项 POC、HNSW 参数与压测网格
schema                dwd_mining_image_vector_detail 字段契约 + 三种物理形态 DDL
                      （Paimon 表 / External Catalog 外部表 / 降级内表）
versioning            embedding_version 版本管理：新旧向量并存、灰度切换、一键回滚
embedding             Embedding 五步流水线（T+1 增量向量化）
index                 双 HNSW 索引、分区级增量刷新、POC 五项验证与参数压测
search                统一检索 API 五步链路 + 四类检索能力 + 向量检索 SQL
backend               档位选型与降级：外部表优先、内表兜底、定时同步 + 主键对账
variant               Paimon Variant 半结构化：vector_meta 的 shredding 与成本模型
client                StarRocks 连接封装（驱动延迟导入，没装也能 import 本包）
render_sql            把上面这些渲染成 flink/sql/vector_*.sql 与 ddl/starrocks_vector.sql
====================  ==========================================================

最小用法
--------
    from adas_lakehouse.vector import (
        SearchRequest, RetrievalMode, ScalarFilters, VectorSearchService, recent_days_window,
    )

    svc = VectorSearchService(encoder=my_clip_encoder)          # 与入湖同一个 CLIP 模型
    resp = svc.search(SearchRequest(
        mode=RetrievalMode.TAG_PLUS_VECTOR,
        text="雨天夜间高速行人横穿",
        filters=recent_days_window(30),                          # 原文示例：最近 30 天
        top_k=50,
    ))
    assert resp.within_sla       # 唯一验收线：千万级单次检索 P95 ≤ 2 秒

外部依赖（StarRocks 驱动 / PyPaimon / PyArrow）全部延迟导入：没装也能 import 本包、
渲染全部 SQL、跑通 dry-run 编排。
"""

from __future__ import annotations

from .backend import (
    MODE_NOTE_CN,
    BackendDecision,
    BackendSelector,
    InternalTableSync,
    ReconcileReport,
    render_reconcile_sql,
    render_sync_sql,
)
from .client import MissingDriverError, QueryResult, RecordingExecutor, SqlExecutor, StarRocksClient
from .embedding import (
    EMBEDDING_STAGE,
    BatchCheckpoint,
    ClipEncoder,
    CostTier,
    EmbeddingPipeline,
    EmbeddingVectors,
    ImageRecord,
    PipelineRunReport,
    RecordingSink,
    StepReport,
    VectorSink,
    Watermark,
    render_mark_refreshed_sql,
    render_upsert_sql,
    resume_key_for,
)
from .index import (
    IMAGE_INDEX_NAME,
    TEXT_INDEX_NAME,
    IndexService,
    PocReport,
    PocResult,
    VectorIndexDef,
    dual_indexes,
    evaluate_poc,
    render_create_index_ddl,
    render_drop_index_ddl,
    render_partition_refresh_sql,
    run_poc_sweep,
    sweep_grid,
    validate_partition_value,
)
from .params import (
    DEFAULT_HNSW_PARAMS,
    DEFAULT_IMAGE_WEIGHT,
    DEFAULT_INDEX_REFRESH_STATUS,
    DEFAULT_METRIC_TYPE,
    DEFAULT_TEXT_WEIGHT,
    DEFAULT_TOP_K,
    DEFAULT_VECTOR_DIM,
    EXAMPLE_SCALAR_FILTER_RECENT_DAYS,
    HNSW_POC_SWEEP_GRID,
    INDEX_REFRESH_DONE,
    INDEX_REFRESH_PENDING,
    INDEX_REFRESH_REFRESHING,
    INDEX_REFRESH_STATUSES,
    MAX_TOP_K,
    PIPELINE_DEADLINE_HOUR,
    PIPELINE_RUNTIME_CN,
    PIPELINE_STEP_COUNT,
    PIPELINE_STEPS,
    POC_CHECK_COUNT,
    POC_CHECKLIST,
    RETRIEVAL_CAPABILITIES,
    SEARCH_P95_SLA_SECONDS,
    SEARCH_SLA_SCALE_CN,
    SIMILARITY_METRIC_NAME,
    VECTOR_STORE_OPTIONS,
    VECTOR_TABLE_SCALE_CN,
    HnswIndexParams,
    PipelineStep,
    PocCheckItem,
    RetrievalCapability,
    RetrievalMode,
    VectorBackend,
    VectorStoreOption,
)
from .schema import (
    CAPTION_COLUMN,
    EMBED_TIME_COLUMN,
    PARTITION_FIELD,
    PRIMARY_KEY,
    SCALAR_FILTER_COLUMNS,
    UPSERT_KEY,
    VECTOR_TABLE_NAME,
    VectorStatus,
    external_table_ref,
    internal_table_ref,
    paimon_table_spec,
    render_external_catalog_ddl,
    render_internal_table_ddl,
    render_paimon_ddl,
    vector_column_names,
    vector_columns_for_projection,
)
from .search import (
    FUSED_SCORE_ALIAS,
    IMAGE_SIM_ALIAS,
    TEXT_SIM_ALIAS,
    ClipQueryEncoder,
    FusionWeights,
    QueryEncoder,
    ScalarFilters,
    SearchHit,
    SearchRequest,
    SearchResponse,
    VectorSearchService,
    as_query_encoder,
    compile_scalar_filters,
    index_path_for,
    prefilter_warning,
    recent_days_window,
    render_enrich_sql,
    render_search_sql,
)
from .variant import (
    BREAKEVEN_READS,
    DUAL_WRITE_COMPARE_DAYS,
    INFER_SHREDDING_OPTIONS,
    PER_READ_SAVING_US,
    SQL_ENGINE_REQUIREMENT,
    VECTOR_META_HOT_PATHS,
    VECTOR_META_SHREDDING_SCHEMA,
    VariantRepresentation,
    breakeven_reads,
    build_meta,
    estimate_total_cost,
    recommend_representation,
    render_variant_get,
    to_variant_array,
    variant_get_column,
    variant_set_paths,
)
from .versioning import (
    ACTIVE_FILTER_CLAUSE,
    EMBEDDING_VERSION_PATTERN,
    EmbeddingVersion,
    VersionRegistry,
    render_activate_sql,
    render_active_filter,
    render_deprecate_sql,
    render_rollback_sql,
    validate_embedding_version,
)

__all__ = [
    # params
    "SEARCH_P95_SLA_SECONDS",
    "PIPELINE_DEADLINE_HOUR",
    "PIPELINE_STEPS",
    "POC_CHECKLIST",
    "RETRIEVAL_CAPABILITIES",
    "SEARCH_SLA_SCALE_CN",
    "VECTOR_TABLE_SCALE_CN",
    "EXAMPLE_SCALAR_FILTER_RECENT_DAYS",
    "PIPELINE_STEP_COUNT",
    "PIPELINE_RUNTIME_CN",
    "POC_CHECK_COUNT",
    "VECTOR_STORE_OPTIONS",
    "DEFAULT_HNSW_PARAMS",
    "HNSW_POC_SWEEP_GRID",
    "DEFAULT_VECTOR_DIM",
    "DEFAULT_METRIC_TYPE",
    "SIMILARITY_METRIC_NAME",
    "DEFAULT_INDEX_REFRESH_STATUS",
    "INDEX_REFRESH_PENDING",
    "INDEX_REFRESH_REFRESHING",
    "INDEX_REFRESH_DONE",
    "INDEX_REFRESH_STATUSES",
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "DEFAULT_IMAGE_WEIGHT",
    "DEFAULT_TEXT_WEIGHT",
    "HnswIndexParams",
    "PipelineStep",
    "PocCheckItem",
    "RetrievalCapability",
    "RetrievalMode",
    "VectorBackend",
    "VectorStoreOption",
    # schema（表结构取自 catalog.registry，本子系统只引用不定义）
    "VECTOR_TABLE_NAME",
    "PARTITION_FIELD",
    "PRIMARY_KEY",
    "UPSERT_KEY",
    "CAPTION_COLUMN",
    "EMBED_TIME_COLUMN",
    "SCALAR_FILTER_COLUMNS",
    "VectorStatus",
    "paimon_table_spec",
    "render_paimon_ddl",
    "render_external_catalog_ddl",
    "render_internal_table_ddl",
    "external_table_ref",
    "internal_table_ref",
    "vector_column_names",
    "vector_columns_for_projection",
    # versioning
    "EmbeddingVersion",
    "VersionRegistry",
    "render_activate_sql",
    "render_deprecate_sql",
    "render_rollback_sql",
    "render_active_filter",
    "validate_embedding_version",
    "EMBEDDING_VERSION_PATTERN",
    "ACTIVE_FILTER_CLAUSE",
    # embedding
    "CostTier",
    "ImageRecord",
    "EmbeddingVectors",
    "ClipEncoder",
    "VectorSink",
    "RecordingSink",
    "Watermark",
    "BatchCheckpoint",
    "EmbeddingPipeline",
    "PipelineRunReport",
    "StepReport",
    "EMBEDDING_STAGE",
    "resume_key_for",
    "render_upsert_sql",
    "render_mark_refreshed_sql",
    # index
    "IMAGE_INDEX_NAME",
    "TEXT_INDEX_NAME",
    "VectorIndexDef",
    "dual_indexes",
    "render_create_index_ddl",
    "render_drop_index_ddl",
    "render_partition_refresh_sql",
    "validate_partition_value",
    "IndexService",
    "PocResult",
    "PocReport",
    "evaluate_poc",
    "sweep_grid",
    "run_poc_sweep",
    # search
    "ScalarFilters",
    "FusionWeights",
    "QueryEncoder",
    "ClipQueryEncoder",
    "as_query_encoder",
    "index_path_for",
    "prefilter_warning",
    "IMAGE_SIM_ALIAS",
    "TEXT_SIM_ALIAS",
    "FUSED_SCORE_ALIAS",
    "SearchRequest",
    "SearchHit",
    "SearchResponse",
    "VectorSearchService",
    "compile_scalar_filters",
    "render_search_sql",
    "render_enrich_sql",
    "recent_days_window",
    # backend
    "BackendDecision",
    "BackendSelector",
    "InternalTableSync",
    "ReconcileReport",
    "render_sync_sql",
    "render_reconcile_sql",
    "MODE_NOTE_CN",
    # variant
    "VariantRepresentation",
    "VECTOR_META_HOT_PATHS",
    "VECTOR_META_SHREDDING_SCHEMA",
    "INFER_SHREDDING_OPTIONS",
    "SQL_ENGINE_REQUIREMENT",
    "BREAKEVEN_READS",
    "PER_READ_SAVING_US",
    "DUAL_WRITE_COMPARE_DAYS",
    "recommend_representation",
    "breakeven_reads",
    "estimate_total_cost",
    "build_meta",
    "render_variant_get",
    "variant_get_column",
    "variant_set_paths",
    "to_variant_array",
    # client
    "SqlExecutor",
    "StarRocksClient",
    "RecordingExecutor",
    "QueryResult",
    "MissingDriverError",
]
