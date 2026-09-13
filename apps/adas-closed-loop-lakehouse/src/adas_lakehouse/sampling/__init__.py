"""分层抽帧：三道成本闸门。

从 TB 级采集数据中提取高价值帧。来源：系列三 · 数据挖掘与 AI 第 2 篇
《分层抽帧策略：从 TB 级采集数据中提取高价值帧：三道成本闸门》
（公众号「小周」，2026-09-10，https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ）。

三道闸门（原文二章）
--------------------
  闸门一 · 常规抽帧（普查）
      触发：全量 clip｜频率：默认 2 秒 1 帧｜保留 1/60（= 0.5fps ÷ 30fps）
      用途：基础场景覆盖，支撑标签统计与粗粒度检索
  闸门二 · 事件抽帧（现场勘查）
      触发：命中规则 / 主动安全触发（AEB 等）/ 驾驶员接管 / 模型低置信度
      频率：事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）
      保留 20/600 = 1/30；天然是二次抽帧，异步补抽不阻塞主链路
      用途：事件上下文精细挖掘
  闸门三 · 推理抽帧（重点取证）
      触发：进入 VLM 推理范围的 clip｜频率：每 clip 打分选 1~5 关键帧
      打分三维：图像清晰度 / 目标丰富度 / 时间位置
      用途：控制最贵的 GPU 推理成本；不产新图，只在存量帧里挑

四个实现要点（原文五章）
------------------------
  多路摄像头同步   frames.group_by_capture_moment
  批流双模式       engine.SamplingMode（Spark 批回刷 / Flink 流实时），共用同一套逻辑
  产物统一落帧表   table.FRAME_TABLE_SPEC → dwd_mining_image_frame_detail
                   （表结构取自 catalog.registry，本子系统不自带定义）
  抽帧前置脱敏校验 compliance.require_desensitized（未脱敏一律拒绝抽帧）

最小用法
--------
    from datetime import datetime
    from adas_lakehouse.sampling import (
        ClipInput, ClipComplianceMarks, EventMarker, EventTriggerType, SamplingPipeline,
    )

    clip = ClipInput(
        data_id="COLLECT_BP_20240115143022_a1b2",
        clip_start=datetime(2024, 1, 15, 14, 30, 22),
        duration_sec=60.0,
        camera_ids=("front", "side-left"),
        compliance=ClipComplianceMarks(
            "COLLECT_BP_20240115143022_a1b2",
            vehicle_side_desensitized=True,
            compliance_cloud_declassified=True,
        ),
        vlm_scope=True,
    )
    result = SamplingPipeline().run(clip)
    print(result.cost.describe())

所有原文数字集中在 ``constants``，本项目补充的设计参数一律带
「⚠️ 原文未明确，本项目设计：」标注。
"""

from __future__ import annotations

from . import constants
from .compliance import (
    ClipComplianceMarks,
    ComplianceDecision,
    ComplianceRejectedError,
    DesensitizationStage,
    DesensitizationStatus,
    check_desensitization,
    require_desensitized,
)
from .constants import (
    EVENT_EXPECTED_FRAMES,
    EVENT_INTERVAL_SECONDS,
    EVENT_KEEP_RATIO,
    EVENT_POST_SECONDS,
    EVENT_PRE_SECONDS,
    EVENT_WINDOW_FULL_FRAMES,
    EVENT_WINDOW_SECONDS,
    FRAME_TABLE_NAME,
    INFERENCE_MAX_KEYFRAMES,
    INFERENCE_MIN_KEYFRAMES,
    NATIVE_VIDEO_FPS,
    ROUTINE_INTERVAL_SECONDS,
    ROUTINE_KEEP_RATIO,
)
from .cost import (
    CostModel,
    GateCostReport,
    PipelineCostReport,
    build_gate_report,
    estimate_pipeline_cost,
    event_frame_count,
    full_frame_count,
    gate_keep_ratio,
    routine_frame_count,
)
from .engine import (
    FfmpegFrameExtractor,
    FrameExtractor,
    FrameWriter,
    InMemoryFrameWriter,
    PaimonFrameWriter,
    PlanOnlyExtractor,
    SamplingEngine,
    SamplingMode,
    build_spark_session,
    register_flink_udfs,
    verify_mode_consistency,
)
from .frames import (
    EventTriggerType,
    FrameGroup,
    FrameRecord,
    ImageId,
    InvalidImageIdError,
    SamplingTier,
    build_image_id,
    derive_camera_position,
    group_by_capture_moment,
    parse_image_id,
)
from .gates import (
    BackfillOutcome,
    ClipInput,
    EventBackfillTask,
    EventGate,
    EventMarker,
    FrameSignalProvider,
    InferenceGate,
    MetadataSignalProvider,
    PipelineResult,
    RoutineGate,
    SamplingPipeline,
    frame_index_to_offset_ms,
    offset_to_frame_index,
)
from .scoring import (
    FrameScore,
    FrameSignals,
    ScoreWeights,
    compute_clarity_score,
    compute_object_richness_score,
    compute_temporal_position_score,
    score_frame,
    score_frames,
    select_keyframes,
    vlm_priority,
)
from .table import (
    FRAME_TABLE_SPEC,
    UPSTREAM_CLIP_SPEC,
    UPSTREAM_FILE_META_SPEC,
    frame_column_names,
    render_frame_table_ddl,
)

__all__ = [
    "constants",
    # 闸门与串联
    "SamplingPipeline",
    "PipelineResult",
    "RoutineGate",
    "EventGate",
    "InferenceGate",
    "ClipInput",
    "EventMarker",
    "EventBackfillTask",
    "BackfillOutcome",
    "offset_to_frame_index",
    "frame_index_to_offset_ms",
    # 帧域模型
    "SamplingTier",
    "EventTriggerType",
    "FrameRecord",
    "FrameGroup",
    "ImageId",
    "InvalidImageIdError",
    "build_image_id",
    "parse_image_id",
    "derive_camera_position",
    "group_by_capture_moment",
    # 打分与选帧
    "ScoreWeights",
    "FrameSignals",
    "FrameScore",
    "FrameSignalProvider",
    "MetadataSignalProvider",
    "compute_clarity_score",
    "compute_object_richness_score",
    "compute_temporal_position_score",
    "score_frame",
    "score_frames",
    "select_keyframes",
    "vlm_priority",
    # 合规闸
    "DesensitizationStage",
    "DesensitizationStatus",
    "ClipComplianceMarks",
    "ComplianceDecision",
    "ComplianceRejectedError",
    "check_desensitization",
    "require_desensitized",
    # 成本账
    "CostModel",
    "GateCostReport",
    "PipelineCostReport",
    "build_gate_report",
    "full_frame_count",
    "routine_frame_count",
    "event_frame_count",
    "gate_keep_ratio",
    "estimate_pipeline_cost",
    # 引擎
    "SamplingMode",
    "SamplingEngine",
    "FrameExtractor",
    "PlanOnlyExtractor",
    "FfmpegFrameExtractor",
    "FrameWriter",
    "InMemoryFrameWriter",
    "PaimonFrameWriter",
    "verify_mode_consistency",
    "register_flink_udfs",
    "build_spark_session",
    # 表
    "FRAME_TABLE_SPEC",
    "UPSTREAM_CLIP_SPEC",
    "UPSTREAM_FILE_META_SPEC",
    "frame_column_names",
    "render_frame_table_ddl",
    # 原文关键数字（便捷再导出）
    "NATIVE_VIDEO_FPS",
    "ROUTINE_INTERVAL_SECONDS",
    "ROUTINE_KEEP_RATIO",
    "EVENT_PRE_SECONDS",
    "EVENT_POST_SECONDS",
    "EVENT_WINDOW_SECONDS",
    "EVENT_INTERVAL_SECONDS",
    "EVENT_EXPECTED_FRAMES",
    "EVENT_WINDOW_FULL_FRAMES",
    "EVENT_KEEP_RATIO",
    "INFERENCE_MIN_KEYFRAMES",
    "INFERENCE_MAX_KEYFRAMES",
    "FRAME_TABLE_NAME",
]
