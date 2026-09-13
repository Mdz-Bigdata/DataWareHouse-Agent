"""统一标签体系：三来源字典映射与去重治理（防标签爆炸）。

来源：系列三《数据闭环统一标签体系设计：三来源标签的字典映射与去重治理》
（小周谈智驾数据闭环 · 系列三 · 数据挖掘与 AI 第 3 篇，2026-09-11）。

一句话：**一套统一标签字典 + 一条三源收口管道 + 一套四态生命周期治理**。

模块地图::

    constants.py   原文出现的全部具体数字与专名（逐字登记，含出处）
    sources.py     三来源画像：采集(人工约定) / 规则(跟规则版本) / 模型(VLM 自由发挥)
    dictionary.py  五大类别受控词表：层级树 + 别名治理 + 四态 + 同层互斥
    mapping.py     管道①字典映射：命中归一到标准 tag_id，未匹配进候选池
    dedup.py       管道②幂等去重 + 冲突消解（联合主键 Upsert / 互斥裁决）
    records.py     RawTag 进、DataTagRecord / ImageTagRecord 出
    pipeline.py    统一标签服务：三步管道、clip→image 继承、caption 特殊处理
    lifecycle.py   候选池 + 四态状态机 + 标签审核流（平台工单 + 双人复核）
    gate.py        质量门禁联动：未审核标签不得进入训练集圈选 + Paimon 时间旅行
    coverage.py    覆盖度度量：日期 × 标签类别，低覆盖 → 定向采集信号
    tables.py      四张表的规格入口——从 catalog.registry 取，本子系统不自带定义

最短用法::

    from adas_lakehouse.tags import RawTag, TagSource, UnifiedTagService

    svc = UnifiedTagService()
    result = svc.ingest([
        RawTag("降雨", TagSource.COLLECT, data_id="COLLECT_BP_20240115143022_a1b2"),
        RawTag("rain", TagSource.MODEL,   data_id="COLLECT_BP_20240115143022_a1b2",
               confidence=0.91, model_name="qwen-vl", model_version="v2.1"),
    ])
    # 两个写法归一到同一个 tag_id，三来源各存一行，不再「查一个漏三个」

SQL 资产：
  · flink/sql/tags_ddl.sql        四张表的建表语句
  · flink/sql/tags_pipeline.sql   三源收口写入 + clip→image 标签继承
  · flink/sql/tags_coverage.sql   覆盖度日指标聚合
  · ddl/starrocks_tags.sql        StarRocks 直查视图与加速物化
"""

from __future__ import annotations

from .constants import (
    ARTICLE_TITLE,
    ARTICLE_URL,
    CAPTION_TAG_CATEGORY,
    COVERAGE_TABLE,
    DATA_TAG_TABLE,
    DICT_CHANGE_REVIEWER_COUNT,
    DICT_TABLE,
    IMAGE_TAG_TABLE,
    LIFECYCLE_STATES,
    PIPELINE_STEPS,
    REFERENCE_PRACTICES,
    TAG_CATEGORY_COUNT,
    TAG_SOURCE_COUNT,
)
from .coverage import (
    LOW_COVERAGE_CONSECUTIVE_DAYS,
    LOW_COVERAGE_RATE_THRESHOLD,
    CoverageReader,
    CoverageRow,
    LowCoverageSignal,
    compute_daily_coverage,
    detect_low_coverage,
)
from .dedup import (
    ConflictDecision,
    ConflictKind,
    ConflictResolver,
    DedupStats,
    IdempotentBuffer,
    cross_source_pairs,
)
from .dictionary import (
    CATEGORY_TAXONOMY,
    SEED_ENTRIES,
    TagCategory,
    TagDictEntry,
    TagDictionary,
    TagLevel,
    TagStatus,
    default_dictionary,
    legacy_scene_tag_entry,
    normalize_tag_text,
    slugify_tag_id,
)
from .gate import GateDecision, RejectReason, TrainingSetGate, apply_review, time_travel_options
from .lifecycle import (
    ALLOWED_TRANSITIONS,
    CANDIDATE_POOL_TTL_DAYS,
    CANDIDATE_PROMOTION_MIN_HITS,
    CandidatePool,
    CandidateTag,
    ChangeRequest,
    ChangeRequestState,
    ChangeRequestType,
    TagLifecycleManager,
    TransitionError,
)
from .mapping import MODEL_TAG_MIN_CONFIDENCE, MappingOutcome, MappingResult, TagMapper
from .pipeline import (
    TAG_STAGE,
    FlinkSqlTagWriter,
    InMemoryTagWriter,
    PipelineResult,
    TagWriter,
    UnifiedTagService,
)
from .records import DataTagRecord, DedupKey, ImageTagRecord, RawTag, ReviewStatus, TagRecord
from .sources import SOURCE_PROFILES, SourceProfile, TagSource, profile_for, source_priority
from .tables import TABLE_NAMES, TABLES, check_row, columns, render_ddl, spec, validate

__all__ = [
    # 原文常量
    "ARTICLE_TITLE",
    "ARTICLE_URL",
    "CAPTION_TAG_CATEGORY",
    "COVERAGE_TABLE",
    "DATA_TAG_TABLE",
    "DICT_TABLE",
    "IMAGE_TAG_TABLE",
    "DICT_CHANGE_REVIEWER_COUNT",
    "LIFECYCLE_STATES",
    "PIPELINE_STEPS",
    "REFERENCE_PRACTICES",
    "TAG_CATEGORY_COUNT",
    "TAG_SOURCE_COUNT",
    # 来源
    "TagSource",
    "SourceProfile",
    "SOURCE_PROFILES",
    "profile_for",
    "source_priority",
    # 字典
    "TagCategory",
    "TagLevel",
    "TagStatus",
    "TagDictEntry",
    "TagDictionary",
    "CATEGORY_TAXONOMY",
    "SEED_ENTRIES",
    "default_dictionary",
    "normalize_tag_text",
    "slugify_tag_id",
    "legacy_scene_tag_entry",
    # 映射
    "TagMapper",
    "MappingOutcome",
    "MappingResult",
    "MODEL_TAG_MIN_CONFIDENCE",
    # 去重与冲突
    "IdempotentBuffer",
    "ConflictResolver",
    "ConflictDecision",
    "ConflictKind",
    "DedupStats",
    "cross_source_pairs",
    # 记录
    "RawTag",
    "TagRecord",
    "DataTagRecord",
    "ImageTagRecord",
    "DedupKey",
    "ReviewStatus",
    # 管道
    "UnifiedTagService",
    "PipelineResult",
    "TagWriter",
    "InMemoryTagWriter",
    "FlinkSqlTagWriter",
    "TAG_STAGE",
    # 生命周期
    "TagLifecycleManager",
    "CandidatePool",
    "CandidateTag",
    "ChangeRequest",
    "ChangeRequestType",
    "ChangeRequestState",
    "ALLOWED_TRANSITIONS",
    "TransitionError",
    "CANDIDATE_PROMOTION_MIN_HITS",
    "CANDIDATE_POOL_TTL_DAYS",
    # 门禁
    "TrainingSetGate",
    "GateDecision",
    "RejectReason",
    "apply_review",
    "time_travel_options",
    # 覆盖度
    "CoverageRow",
    "LowCoverageSignal",
    "compute_daily_coverage",
    "detect_low_coverage",
    "CoverageReader",
    "LOW_COVERAGE_RATE_THRESHOLD",
    "LOW_COVERAGE_CONSECUTIVE_DAYS",
    # 表（结构取自 catalog.registry）
    "TABLES",
    "TABLE_NAMES",
    "spec",
    "columns",
    "check_row",
    "render_ddl",
    "validate",
]
