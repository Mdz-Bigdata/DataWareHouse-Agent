"""三道成本闸门的判定、抽帧计划与串联。

原文二章的闸门表（逐字）：
  | 抽帧层级 | 触发条件 | 频率 | 用途 |
  | 常规抽帧 | 全量 clip | 默认 2 秒 1 帧 | 基础场景覆盖，支撑标签统计与粗粒度检索 |
  | 事件抽帧 | 命中规则 / 主动安全触发（AEB 等）/ 驾驶员接管 / 模型低置信度 |
             事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧） |
             事件上下文精细挖掘，可异步补抽 |
  | 推理抽帧 | 进入 VLM 推理范围的 clip | 每 clip 打分选 1~5 关键帧 |
             按清晰度/目标丰富度/时间位置选帧，控制推理成本 |

原文三章的时序细节（决定了闸门二必须异步）：
  "事件信息本身依赖规则引擎的输出，所以事件抽帧天然是「二次抽帧」——常规抽帧先行，
   规则引擎事后识别出事件，再回头对对应窗口补抽。这就是「异步补抽」机制：调度上把
   事件抽帧任务挂在规则结果之后，补抽与主链路解耦，谁也不阻塞谁。"

帧序号约定（⚠️ 原文未明确，本项目设计）：
  frame_index = round(相对 clip 起点的秒数 × 30)，即原生帧号（30 来自原文
  "10 秒 30 帧"）。这么定有三个好处：
    ① 字典序 == 时间序；
    ② 闸门一（2 秒 1 帧 → 索引 0/60/120…）与闸门二（1 秒 1 帧 → 索引 …/30/60/90…）
       在重叠时刻自然得到同一个 frame_index，也就是同一个 image_id，
       主键 Upsert 天然去重，不会因为补抽而写出重复图；
    ③ 任何时刻都能反算回原始视频的帧位置，便于重刷对齐。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from ..ids import new_run_id
from . import constants as K
from .compliance import (
    ClipComplianceMarks,
    ComplianceRejectedError,
    DesensitizationStatus,
    check_desensitization,
    require_desensitized,
)
from .cost import CostModel, PipelineCostReport, build_gate_report, full_frame_count
from .frames import (
    EventTriggerType,
    FrameGroup,
    FrameRecord,
    SamplingTier,
    build_image_id,
    group_by_capture_moment,
)
from .scoring import FrameSignals, ScoreWeights, score_frames, select_keyframes, vlm_priority

logger = logging.getLogger(__name__)

__all__ = [
    "ClipInput",
    "EventMarker",
    "EventBackfillTask",
    "BackfillOutcome",
    "FrameSignalProvider",
    "MetadataSignalProvider",
    "RoutineGate",
    "EventGate",
    "InferenceGate",
    "SamplingPipeline",
    "PipelineResult",
    "offset_to_frame_index",
    "frame_index_to_offset_ms",
]


def offset_to_frame_index(offset_seconds: float) -> int:
    """相对 clip 起点的秒数 → 原生帧号（30 fps）。见模块 docstring 的帧序号约定。"""
    if offset_seconds < 0:
        raise ValueError(f"帧偏移不能为负，收到 {offset_seconds}")
    return int(round(offset_seconds * K.NATIVE_VIDEO_FPS))


def frame_index_to_offset_ms(frame_index: int) -> int:
    """原生帧号 → 相对 clip 起点的毫秒偏移。"""
    if frame_index < 0:
        raise ValueError(f"帧序号不能为负，收到 {frame_index}")
    return int(round(frame_index * 1000 / K.NATIVE_VIDEO_FPS))


# --------------------------------------------------------------------------- 输入


@dataclass(slots=True)
class ClipInput:
    """闸门的输入：一个合规入湖的 clip。

    字段全部来自采集域既有表（原文一章："clip 的元数据完全复用采集域既有表"）：
    dwd_collect_clip_detail + ods_data_file_meta。挖掘平台只消费，不改接入链路。
    """

    data_id: str
    clip_start: datetime
    duration_sec: float
    #: 多路摄像头（原文五章："同一时刻有前视、侧视、后视等多路摄像头"）
    camera_ids: tuple[str, ...]
    project_code: str = ""
    vehicle_code: str = ""
    compliance: ClipComplianceMarks | None = None
    #: 每路摄像头的视频对象存储 key，用于生成帧文件路径
    video_object_keys: dict[str, str] = field(default_factory=dict)
    gps_lat: float | None = None
    gps_lon: float | None = None
    #: camera_id → camera_position（front/left/right/rear）。不传则由
    #: frames.derive_camera_position 从 camera_id 推断；车型配置表有权威映射时用它覆盖。
    camera_positions: dict[str, str] = field(default_factory=dict)
    #: camera_id → (宽, 高) 像素。分辨率随车型与摄像头代际变化（catalog 对
    #: image_width/image_height 的列注释），训练侧要按它做尺寸对齐，所以逐帧落表。
    camera_resolutions: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: 该 clip 是否进入 VLM 推理范围（闸门三的触发条件）
    vlm_scope: bool = False
    #: 场景切换时刻（相对 clip 起点的秒数），供时间位置打分使用。
    #: ⚠️ 原文未明确，本项目设计：原文说 "场景切换时刻的帧优先" 但没说场景切换从哪来；
    #: 本项目把它当作上游（规则引擎/场景识别）给出的输入，抽帧侧只消费。
    scene_change_offsets_sec: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.duration_sec < 0:
            raise ValueError(f"clip 时长不能为负：{self.duration_sec}")
        if not self.camera_ids:
            raise ValueError(f"clip {self.data_id} 至少要有一路摄像头")
        if len(set(self.camera_ids)) != len(self.camera_ids):
            raise ValueError(
                f"clip {self.data_id} 的 camera_ids 有重复：{self.camera_ids}——"
                "重复会让同一路相机在同一时刻产出两条同 image_id 的帧"
            )
        for cam, position in self.camera_positions.items():
            if position not in K.CAMERA_POSITIONS:
                raise ValueError(
                    f"camera_positions[{cam!r}]={position!r} 不在取值域 {K.CAMERA_POSITIONS}"
                )
        for cam, wh in self.camera_resolutions.items():
            if len(wh) != 2 or wh[0] <= 0 or wh[1] <= 0:
                raise ValueError(f"camera_resolutions[{cam!r}]={wh!r} 必须是正的 (宽, 高)")

    @property
    def clip_end(self) -> datetime:
        return self.clip_start + timedelta(seconds=self.duration_sec)

    @property
    def camera_count(self) -> int:
        return len(self.camera_ids)

    def contains(self, moment: datetime) -> bool:
        return self.clip_start <= moment <= self.clip_end

    def frame_path(self, camera_id: str, frame_index: int) -> str:
        """帧图片的对象存储路径。

        ⚠️ 原文未明确，本项目设计：原文只说帧表里有 "文件路径"，没给路径规范。
        本项目用 ``frames/{data_id}/{camera_id}/{frame_index:06d}.jpg``——
        以 data_id 为目录首段，便于按 clip 整体做生命周期与删除。
        """
        return f"frames/{self.data_id}/{camera_id}/{frame_index:06d}.jpg"


@dataclass(frozen=True, slots=True)
class EventMarker:
    """规则引擎事后识别出的一个事件（闸门二的触发源）。

    原文三章："触发条件有四类：命中挖掘规则、主动安全触发（AEB 等）、驾驶员接管、
    模型低置信度——每一类都对应一种「模型表现与预期有偏差」的信号。"
    """

    data_id: str
    trigger_type: EventTriggerType
    #: 事件发生时刻（绝对时间），窗口围绕它取前 15 后 5
    event_time: datetime
    #: 规则引擎产出该事件的时刻，用于异步补抽的时延统计
    detected_at: datetime | None = None
    #: 触发来源标识（规则 ID / AEB 事件 ID / 接管记录 ID …）
    source_id: str = ""
    #: 上游产物 ID，落 parent_artifact_id 做血缘兜底
    parent_artifact_id: str = ""

    def window(self) -> tuple[datetime, datetime]:
        """事件窗口：前 15 秒 + 后 5 秒，共 20 秒（原文三章，逐字）。

        窗口不对称的原因（原文）："事件的价值主要在「它是怎么发生的」——前车切入、
        行人闯入、信号灯变化的过程都在事件之前；事件之后的 5 秒则用来确认后果
        （是否制动、是否绕行）。"
        """
        return (
            self.event_time - timedelta(seconds=K.EVENT_PRE_SECONDS),
            self.event_time + timedelta(seconds=K.EVENT_POST_SECONDS),
        )


@dataclass(slots=True)
class EventBackfillTask:
    """一个异步补抽任务：挂在规则结果之后，与主链路解耦。

    原文三章："调度上把事件抽帧任务挂在规则结果之后，补抽与主链路解耦，谁也不阻塞谁。"

    ⚠️ 原文未明确，本项目设计：TTL 与最大重试次数原文没给，默认取
    constants.EVENT_BACKFILL_TTL_SECONDS（24 小时）与
    constants.EVENT_BACKFILL_MAX_RETRIES（3 次）——超时即判失败并告警，
    避免补抽任务无限堆积在队列里。
    """

    marker: EventMarker
    enqueued_at: datetime
    attempts: int = 0
    ttl_seconds: int = K.EVENT_BACKFILL_TTL_SECONDS
    max_retries: int = K.EVENT_BACKFILL_MAX_RETRIES

    def is_expired(self, now: datetime) -> bool:
        return (now - self.enqueued_at).total_seconds() > self.ttl_seconds

    def can_retry(self) -> bool:
        return self.attempts < self.max_retries

    def backfill_latency_seconds(self) -> float | None:
        """规则识别到入队的时延，用于观测「补抽是否真的没阻塞主链路」。"""
        if self.marker.detected_at is None:
            return None
        return (self.enqueued_at - self.marker.detected_at).total_seconds()


@dataclass(frozen=True, slots=True)
class BackfillOutcome:
    """一次补抽任务的结论。

    补抽是与主链路解耦的独立调度（原文三章），所以它的失败不能靠抛异常传递——
    调用方拿到的是一份结论，据此决定重排、告警还是放弃。
    """

    task: EventBackfillTask
    created: list[FrameRecord]
    succeeded: bool
    reason: str = ""

    @property
    def trigger_type(self) -> EventTriggerType:
        return self.task.marker.trigger_type

    def describe(self) -> str:
        head = "补抽成功" if self.succeeded else "补抽失败"
        return (
            f"{head}: trigger={self.trigger_type.name_cn} "
            f"event_time={self.task.marker.event_time} 新增 {len(self.created)} 帧"
            + (f"；{self.reason}" if self.reason else "")
        )


# --------------------------------------------------------------------------- 信号源


class FrameSignalProvider(Protocol):
    """打分信号的来源。真实实现接 CV 模型；默认实现只用元数据。"""

    def __call__(self, frame: FrameRecord, clip: ClipInput) -> FrameSignals:  # pragma: no cover
        ...


@dataclass(slots=True)
class MetadataSignalProvider:
    """只靠元数据出信号的默认实现——不解码像素，零 GPU 成本。

    能算准的只有「时间位置」这一维（事件时刻与场景切换时刻都在元数据里）。
    另外两维需要 CV 模型：

    ⚠️ 原文未明确，本项目设计：清晰度默认给「无劣化」(blur/过曝/遮挡 全 0 →
    frame_quality_score = 1.0)，目标丰富度默认给 0（未检测 = 不加分）。
    这是刻意的保守取值——没检测就不要假装检测过了。真实链路请注入接了
    模糊检测/目标检测的 provider，本类只用于 dry-run 与单测。
    """

    #: 场景切换判定的容忍窗口（秒）。⚠️ 原文未明确，本项目设计：取半个常规抽帧间隔。
    scene_change_tolerance_sec: float = K.ROUTINE_INTERVAL_SECONDS / 2

    def __call__(self, frame: FrameRecord, clip: ClipInput) -> FrameSignals:
        seconds_from_event: float | None = None
        if frame.event_time is not None:
            seconds_from_event = (frame.frame_timestamp - frame.event_time).total_seconds()

        offset_sec = frame.clip_offset_ms / 1000.0
        is_scene_change = any(
            abs(offset_sec - sc) <= self.scene_change_tolerance_sec
            for sc in clip.scene_change_offsets_sec
        )
        return FrameSignals(
            seconds_from_event=seconds_from_event,
            is_scene_change=is_scene_change,
        )


# --------------------------------------------------------------------------- 闸门一


@dataclass(slots=True)
class RoutineGate:
    """闸门一 · 常规抽帧（普查）。

    触发条件：全量 clip。频率：默认 2 秒 1 帧。
    用途：基础场景覆盖，支撑标签统计与粗粒度检索。

    这道闸门的成本账：保留 1/60（= 2 秒 1 帧 ÷ 30 fps），过滤掉 59/60。
    它是三道闸门里唯一「无条件对所有数据执行」的一道——所以单位帧成本最敏感。
    """

    interval_seconds: float = float(K.ROUTINE_INTERVAL_SECONDS)
    algo_version: str = K.SAMPLING_ALGO_VERSION_DEFAULT

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError(f"常规抽帧间隔必须为正，收到 {self.interval_seconds}")

    def offsets(self, clip: ClipInput) -> list[float]:
        """本 clip 的抽帧时刻（相对起点的秒数），从 0 起按间隔推进。

        用「序号 × 间隔」而不是「t += 间隔」累加：浮点累加在长 clip 上会漂移，
        漂到 ±1/30 秒就会把 :func:`offset_to_frame_index` 的 round 推到隔壁帧号，
        进而产出一个和常规网格对不齐的 image_id——事件抽帧的去重就此失效，
        同一瞬间的画面会在帧表里留下两行。
        """
        if clip.duration_sec <= 0:
            return []
        count = int(math.ceil(clip.duration_sec / self.interval_seconds))
        out = [i * self.interval_seconds for i in range(count)]
        # ceil 可能多给一格（duration 恰为间隔整数倍时不会，但浮点时长会）
        return [t for t in out if t < clip.duration_sec]

    def plan(self, clip: ClipInput, run_id: str = "") -> list[FrameRecord]:
        """产出常规抽帧的帧计划（逐路摄像头，多路同刻会被分到同一组）。"""
        frames: list[FrameRecord] = []
        for offset in self.offsets(clip):
            index = offset_to_frame_index(offset)
            for camera_id in clip.camera_ids:
                frames.append(
                    _make_frame(
                        clip,
                        camera_id,
                        index,
                        tier=SamplingTier.ROUTINE,
                        interval_sec=self.interval_seconds,
                        run_id=run_id,
                        algo_version=self.algo_version,
                    )
                )
        return frames


# --------------------------------------------------------------------------- 闸门二


@dataclass(slots=True)
class EventGate:
    """闸门二 · 事件抽帧（现场勘查）。

    触发条件：命中规则 / 主动安全触发（AEB 等）/ 驾驶员接管 / 模型低置信度。
    频率：事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）。
    用途：事件上下文精细挖掘，可异步补抽。

    成本账：保留 20/600 = 1/30——原文 "1 秒 1 帧的密度足够还原因果链，
    又不至于把 20 秒变成 600 张全量帧"。

    去重：窗口内与常规抽帧重合的时刻会命中同一个 frame_index / image_id，
    本闸门不再重复产帧，而是把事件上下文（trigger_type / event_time / 窗口）
    附加到已有的那一帧上。
    ⚠️ 原文未明确，本项目设计：原文只说 "三路产物统一写入 dwd_mining_image_frame_detail"，
    没说重合帧算哪一层。本项目的口径是「谁先产出算谁的层级」，事件上下文单独落字段——
    这样 sampling_tier 忠实反映帧的来源，事件覆盖情况靠 event_trigger_type 查。
    """

    interval_seconds: float = float(K.EVENT_INTERVAL_SECONDS)
    pre_seconds: float = float(K.EVENT_PRE_SECONDS)
    post_seconds: float = float(K.EVENT_POST_SECONDS)
    algo_version: str = K.SAMPLING_ALGO_VERSION_DEFAULT

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError(f"事件抽帧间隔必须为正，收到 {self.interval_seconds}")
        if self.pre_seconds < 0 or self.post_seconds < 0:
            raise ValueError("事件窗口前后长度不能为负")

    @property
    def window_seconds(self) -> float:
        """窗口总长。默认 15 + 5 = 20，与原文 "共 20 秒" 一致。"""
        return self.pre_seconds + self.post_seconds

    def offsets(self, clip: ClipInput, marker: EventMarker) -> list[float]:
        """窗口内的抽帧时刻（相对 clip 起点的秒数）。

        取左闭右开 [事件-15, 事件+5)，步长 1 秒 → 恰好 20 个采样点，
        对应原文的 "约 20 帧"。窗口会与 clip 边界求交——事件贴着 clip 头尾时
        自然少几帧，这也是原文说 "约" 而不是 "恰好" 的现实原因之一。
        """
        start, end = marker.window()
        out: list[float] = []
        steps = int(math.ceil(self.window_seconds / self.interval_seconds))
        for i in range(steps):
            moment = start + timedelta(seconds=i * self.interval_seconds)
            if moment < clip.clip_start or moment >= clip.clip_end or moment >= end:
                continue
            out.append((moment - clip.clip_start).total_seconds())
        return out

    def covers(self, clip: ClipInput, marker: EventMarker) -> bool:
        """事件窗口与 clip 是否有交集。

        规则引擎给的事件时刻可能落在别的 clip 上（跨 clip 的事件、或上游对错了时间轴）。
        这时窗口与本 clip 无交集，:meth:`offsets` 会安静地返回空列表——补抽任务看起来
        「跑成功了」却一帧没产。本方法把这种情况显式化，供 :meth:`plan` 告警。
        """
        start, end = marker.window()
        return start < clip.clip_end and end > clip.clip_start

    def plan(
        self,
        clip: ClipInput,
        markers: Sequence[EventMarker],
        existing: Iterable[FrameRecord] = (),
        run_id: str = "",
    ) -> tuple[list[FrameRecord], list[FrameRecord]]:
        """产出事件抽帧计划。

        Args:
            clip: 目标 clip。
            markers: 规则引擎事后给出的事件列表。
            existing: 已存在的帧（通常是闸门一的产物），用于去重与上下文附加。
            run_id: 本次补抽运行的 run_id。

        Returns:
            (新增帧, 被附加了事件上下文的已有帧)
        """
        index_map: dict[tuple[str, int], FrameRecord] = {
            (f.camera_id, f.frame_index): f for f in existing
        }
        created: list[FrameRecord] = []
        # 按 id() 去重而不是 ``if hit not in enriched``：FrameRecord 是 eq=True 的
        # dataclass，``in`` 走的是逐字段相等比较——既是 O(n²)，又会把两条字段恰好
        # 相同的不同帧判成同一条。这里要的是「同一个对象只登记一次」，即身份去重。
        enriched_by_id: dict[int, FrameRecord] = {}

        for marker in markers:
            if marker.data_id != clip.data_id:
                raise ValueError(
                    f"事件 {marker.source_id or marker.trigger_type.value} 的 data_id "
                    f"{marker.data_id!r} 与 clip {clip.data_id!r} 不符"
                )
            if not self.covers(clip, marker):
                logger.warning(
                    "事件窗口与 clip 无交集，本次补抽零产出：clip=%s 窗口=%s trigger=%s",
                    clip.data_id,
                    marker.window(),
                    marker.trigger_type.value,
                )
                continue
            win_start, win_end = marker.window()
            for offset in self.offsets(clip, marker):
                index = offset_to_frame_index(offset)
                for camera_id in clip.camera_ids:
                    key = (camera_id, index)
                    hit = index_map.get(key)
                    if hit is not None:
                        _attach_event_context(hit, marker, win_start, win_end)
                        enriched_by_id.setdefault(id(hit), hit)
                        continue
                    frame = _make_frame(
                        clip,
                        camera_id,
                        index,
                        tier=SamplingTier.EVENT,
                        interval_sec=self.interval_seconds,
                        run_id=run_id,
                        algo_version=self.algo_version,
                    )
                    _attach_event_context(frame, marker, win_start, win_end)
                    index_map[key] = frame
                    created.append(frame)
        return created, list(enriched_by_id.values())

    def enqueue(
        self, markers: Sequence[EventMarker], now: datetime | None = None
    ) -> list[EventBackfillTask]:
        """把事件包装成异步补抽任务——挂在规则结果之后，不阻塞主链路。"""
        moment = now or datetime.now()
        return [EventBackfillTask(m, moment) for m in markers]


# --------------------------------------------------------------------------- 闸门三


@dataclass(slots=True)
class InferenceGate:
    """闸门三 · 推理抽帧（重点取证）。

    触发条件：进入 VLM 推理范围的 clip。频率：每 clip 打分选 1~5 关键帧。
    用途：按清晰度/目标丰富度/时间位置选帧，控制推理成本。

    注意这道闸门**不产生新图片**——它在闸门一、二已经落湖的帧里挑，
    所以它削的是 GPU 成本（"GPU 推理成本与送进去的图片数量成正比"），不是存储成本。
    """

    min_keyframes: int = K.INFERENCE_MIN_KEYFRAMES
    max_keyframes: int = K.INFERENCE_MAX_KEYFRAMES
    score_floor: float = K.KEYFRAME_SCORE_FLOOR
    min_spacing_seconds: float = K.KEYFRAME_MIN_SPACING_SECONDS
    #: 普通帧进入推理候选池的抽样比例。原文四章末尾引上一篇的向量化成本分级：
    #: "规则命中、事件抽帧产出的帧优先进 VLM，普通帧抽样"。
    #: ⚠️ 原文未明确，本项目设计：比例默认 0.1，见 constants.ORDINARY_FRAME_VLM_SAMPLE_RATIO。
    ordinary_sample_ratio: float = K.ORDINARY_FRAME_VLM_SAMPLE_RATIO
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    signal_provider: FrameSignalProvider = field(default_factory=MetadataSignalProvider)

    def __post_init__(self) -> None:
        if not 0.0 < self.ordinary_sample_ratio <= 1.0:
            raise ValueError(f"普通帧抽样比例必须落在 (0, 1]，收到 {self.ordinary_sample_ratio}")

    def score_all(self, clip: ClipInput, frames: Sequence[FrameRecord]) -> None:
        """给**每一帧**打分并写回，不只给候选帧打。

        原文四章："frame_quality_score 作为帧表字段落湖，选帧逻辑调整时可以重算分数
        重新圈选，历史推理结果也能按当时的分数复盘。" ——只给候选帧打分的话，没进
        候选池的帧就是 NULL 分，「重新圈选」时根本没有可比的底数，复盘也无从谈起。
        所以打分的范围是全量落湖帧，收窄只发生在 :meth:`candidates` 这一步。
        """
        score_frames(
            ((f, self.signal_provider(f, clip)) for f in frames),
            weights=self.weights,
            write_back=True,
        )

    def candidates(self, clip: ClipInput, frames: Sequence[FrameRecord]) -> list[FrameRecord]:
        """圈定推理候选池：优先帧全进，普通帧按比例抽样。

        原文四章末尾（引系列三第 1 篇的向量化成本分级）："规则命中、事件抽帧产出的帧
        优先进 VLM，普通帧抽样——推理预算始终流向信息密度最高的图片。"
        「优先」的判定见 :func:`scoring.vlm_priority`。

        ⚠️ 原文未明确，本项目设计：抽样用「每路相机内每 stride 帧取一帧」的确定性
        步长，而不是随机数。stride = round(1 / ordinary_sample_ratio)，默认 10。
        确定性是硬要求——同一个 clip 重跑必须圈出同一批候选，否则 artifact_id 的
        幂等性与「按当时的分数复盘」都不成立。

        Returns:
            候选帧（时间升序）；未进入 VLM 范围的 clip 返回空列表。
        """
        if not clip.vlm_scope or not frames:
            return []
        stride = max(1, int(round(1.0 / self.ordinary_sample_ratio)))
        picked: list[FrameRecord] = []
        for camera_id in clip.camera_ids:
            per_camera = sorted(
                (f for f in frames if f.camera_id == camera_id), key=lambda f: f.clip_offset_ms
            )
            ordinary_seen = 0
            kept_any = False
            for frame in per_camera:
                if vlm_priority(frame):
                    picked.append(frame)
                    kept_any = True
                    continue
                if ordinary_seen % stride == 0:
                    picked.append(frame)
                    kept_any = True
                ordinary_seen += 1
            if not kept_any and per_camera:
                # 兜底：这一路一帧都没抽中（stride 大于该路帧数时会发生）。
                # 原文的下限是「每 clip 1~5 关键帧」，下限是 1 不是 0，候选池不能为空。
                picked.append(per_camera[0])
        picked.sort(key=lambda f: (f.clip_offset_ms, f.camera_id))
        return picked

    def pick(self, clip: ClipInput, candidates: Sequence[FrameRecord]) -> list[FrameRecord]:
        """在**已打过分**的候选池里选出 1~5 张关键帧。

        逐路摄像头分别选——原文说的是 "每 clip 打分选 1~5 关键帧"，
        ⚠️ 原文未明确，本项目设计：多路摄像头场景下按 camera 维度分别应用 1~5 区间，
        否则前视一路就会吃掉整个 clip 的关键帧配额，侧视/后视永远选不上。
        """
        if not clip.vlm_scope or not candidates:
            return []
        selected: list[FrameRecord] = []
        for camera_id in clip.camera_ids:
            per_camera = [f for f in candidates if f.camera_id == camera_id]
            if not per_camera:
                continue
            selected.extend(
                select_keyframes(
                    per_camera,
                    min_k=self.min_keyframes,
                    max_k=self.max_keyframes,
                    score_floor=self.score_floor,
                    min_spacing_seconds=self.min_spacing_seconds,
                )
            )
        selected.sort(key=lambda f: (f.clip_offset_ms, f.camera_id))
        return selected

    def select(self, clip: ClipInput, frames: Sequence[FrameRecord]) -> list[FrameRecord]:
        """一把跑完闸门三：全量打分 → 圈候选池 → 选 1~5 张关键帧。

        供外部单独调用；串联执行时 :class:`SamplingPipeline` 分三步调，
        因为它还要拿候选池的大小去结闸门三那道账。

        Returns:
            按时间升序的关键帧；未进入 VLM 范围的 clip 返回空列表。
        """
        if not clip.vlm_scope or not frames:
            return []
        self.score_all(clip, frames)
        return self.pick(clip, self.candidates(clip, frames))


# --------------------------------------------------------------------------- 串联


@dataclass(slots=True)
class PipelineResult:
    """三道闸门跑完的产物。"""

    clip: ClipInput
    run_id: str
    #: 全部落 dwd_mining_image_frame_detail 的帧（闸门一 + 闸门二），已去重
    frames: list[FrameRecord]
    #: 闸门三选出的关键帧（是 frames 的子集，仅打标 is_keyframe）
    keyframes: list[FrameRecord]
    #: 多路摄像头同步分组
    groups: list[FrameGroup]
    cost: PipelineCostReport
    backfill_tasks: list[EventBackfillTask] = field(default_factory=list)
    #: 闸门三的候选池，供 keyframe_candidates 暴露（内部字段，构造时由 pipeline 填）
    _candidates: list[FrameRecord] = field(default_factory=list)

    @property
    def routine_frames(self) -> list[FrameRecord]:
        return [f for f in self.frames if f.tier is SamplingTier.ROUTINE]

    @property
    def event_frames(self) -> list[FrameRecord]:
        return [f for f in self.frames if f.tier is SamplingTier.EVENT]

    @property
    def keyframe_candidates(self) -> list[FrameRecord]:
        """闸门三的候选池（优先帧 + 抽样后的普通帧）。关键帧从这里面选。"""
        return list(self._candidates)

    def incomplete_groups(self) -> list[FrameGroup]:
        """多路没齐的同步组。

        原文五章："同一时刻的多路图片作为一组样本……训练时可按视角组合"——
        少一路的组不能当一组样本用。某一路相机中途掉线/丢帧时，这里会把受影响的
        时刻列出来，调用方据此决定是丢弃该组还是降级成单视角样本。
        """
        return [g for g in self.groups if not g.is_complete(self.clip.camera_ids)]

    def rows(self) -> list[dict]:
        """可直接交给 writer 的行字典列表。"""
        return [f.to_row() for f in self.frames]


@dataclass(slots=True)
class SamplingPipeline:
    """三道成本闸门的串联执行器。

    执行顺序严格对齐原文的时序：
      0. 抽帧前置脱敏校验——未脱敏一律拒绝，整个 clip 不启动（合规红线）；
      1. 闸门一 常规抽帧，全量 clip，2 秒 1 帧；
      2. 闸门二 事件抽帧，依赖规则引擎输出，天然是二次抽帧 / 异步补抽；
      3. 闸门三 推理抽帧，在已落湖的帧里打分选 1~5 张关键帧。

    ``markers`` 为空时 run() 只跑闸门一——这正是「主链路不等补抽」的形态；
    补抽任务由 ``EventGate.enqueue`` 产出，事后再调 ``backfill()`` 补上。
    """

    routine: RoutineGate = field(default_factory=RoutineGate)
    event: EventGate = field(default_factory=EventGate)
    inference: InferenceGate = field(default_factory=InferenceGate)
    cost_model: CostModel = field(default_factory=CostModel)
    #: 未脱敏是否直接抛异常。默认 True——原文是 "一律拒绝抽帧"，没有降级路径。
    strict_compliance: bool = True

    def run(
        self,
        clip: ClipInput,
        markers: Sequence[EventMarker] = (),
        *,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> PipelineResult:
        """跑完三道闸门。

        Args:
            clip: 输入 clip。
            markers: 规则引擎给出的事件；为空表示本次只跑主链路（闸门一）。
            run_id: 三级 ID 的 run_id；不传则新建一个 ``run_sampling_*``。
            now: 补抽任务入队时间，便于测试注入。

        Returns:
            PipelineResult，含帧、关键帧、多路分组、成本账与补抽任务。

        Raises:
            ComplianceRejectedError: 未完成双脱敏（strict_compliance=True 时）。
        """
        rid = run_id or new_run_id(K.SAMPLING_STAGE).raw
        desens_status = self._check_compliance(clip)

        # ---- 闸门一 ----
        routine_frames = self.routine.plan(clip, run_id=rid)

        # ---- 闸门二 ----
        created, enriched = self.event.plan(clip, markers, existing=routine_frames, run_id=rid)
        all_frames = routine_frames + created

        for frame in all_frames:
            frame.desensitization_status = desens_status
            frame.assign_artifact_id()

        # ---- 多路摄像头同步 ----
        groups = group_by_capture_moment(all_frames)

        # ---- 闸门三 ----（打分覆盖全量帧，选帧只在候选池里做）
        self.inference.score_all(clip, all_frames)
        candidates = self.inference.candidates(clip, all_frames)
        keyframes = self.inference.pick(clip, candidates)

        cost = self._build_cost(
            clip,
            routine_out=len(routine_frames),
            event_new=len(created),
            event_covered=len(created) + len(enriched),
            inference_in=len(candidates),
            keyframe_out=len(keyframes),
        )
        tasks = self.event.enqueue(markers, now=now) if markers else []
        return PipelineResult(
            clip, rid, all_frames, keyframes, groups, cost, tasks, list(candidates)
        )

    def backfill(
        self,
        clip: ClipInput,
        markers: Sequence[EventMarker],
        existing: Sequence[FrameRecord],
        *,
        run_id: str | None = None,
    ) -> list[FrameRecord]:
        """异步补抽：规则结果就绪后，回头对事件窗口补抽。

        与 run() 分开是为了如实反映原文的解耦要求——补抽是独立调度的任务，
        它读已落湖的常规帧做去重，只写新增的那部分。

        Returns:
            新增的事件帧（已分配 artifact_id）。已有帧只被附加事件上下文，不重复返回。
        """
        rid = run_id or new_run_id(K.SAMPLING_STAGE).raw
        desens_status = self._check_compliance(clip)
        created, _ = self.event.plan(clip, markers, existing=existing, run_id=rid)
        for frame in created:
            frame.desensitization_status = desens_status
            frame.assign_artifact_id()
        # 多路摄像头同步：补抽帧必须和已落湖的帧一起重新分组，否则补出来的帧
        # frame_group_id 是空的——原文五章要求「同一时刻的多路图片作为一组样本」，
        # 只在 run() 里分组、补抽路径不分组，等于事件窗口里最该成组的那 20 秒反而没组。
        # 分组键是 (data_id, 毫秒偏移)，与 run() 同一套确定性规则，已有帧的组 ID 不会漂。
        group_by_capture_moment(list(existing) + created)
        return created

    def run_backfill_task(
        self,
        clip: ClipInput,
        task: EventBackfillTask,
        existing: Sequence[FrameRecord],
        *,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> BackfillOutcome:
        """执行一个异步补抽任务，带 TTL 与重试闸。

        这是 :class:`EventBackfillTask` 的消费方——没有它，``ttl_seconds`` 与
        ``max_retries`` 就只是两个没人读的字段，补抽队列里的僵尸任务会一直堆着。
        原文三章只要求「补抽与主链路解耦，谁也不阻塞谁」，TTL 与重试上限是本项目
        为「解耦不等于放任」补的闸（⚠️ 原文未明确，本项目设计，见 constants）。

        判定顺序（先判死，再判重试，最后才真跑）：
          1. 入队超过 ``ttl_seconds`` → 直接判失败，不再尝试；
          2. ``attempts`` 已达 ``max_retries`` → 判失败，重试预算用尽；
          3. 计一次 attempt，跑 :meth:`backfill`；异常转成失败结论而不是往上抛——
             一个 clip 补抽失败不该让整条补抽队列停摆。

        Returns:
            BackfillOutcome。``succeeded=False`` 时 ``reason`` 说明是超时、重试耗尽
            还是执行异常；``created`` 为本次新增的事件帧。
        """
        moment = now or datetime.now()
        if task.is_expired(moment):
            reason = f"补抽任务超时：入队于 {task.enqueued_at}，已超过 TTL {task.ttl_seconds} 秒"
            logger.error("clip=%s %s", clip.data_id, reason)
            return BackfillOutcome(task, [], False, reason)
        if not task.can_retry():
            reason = f"补抽重试预算用尽：已尝试 {task.attempts} 次，上限 {task.max_retries} 次"
            logger.error("clip=%s %s", clip.data_id, reason)
            return BackfillOutcome(task, [], False, reason)

        task.attempts += 1
        try:
            created = self.backfill(clip, [task.marker], existing, run_id=run_id)
        except ComplianceRejectedError:
            raise  # 合规红线不吞：未脱敏一律拒绝抽帧，且必须让调用方看见
        except Exception as exc:  # noqa: BLE001 - 单个任务失败不拖垮整条补抽队列
            reason = f"补抽执行失败（第 {task.attempts} 次尝试）：{type(exc).__name__}: {exc}"
            logger.exception("clip=%s 补抽失败", clip.data_id)
            return BackfillOutcome(task, [], False, reason)
        return BackfillOutcome(task, created, True)

    def process_backfill_queue(
        self,
        clip: ClipInput,
        tasks: Sequence[EventBackfillTask],
        existing: Sequence[FrameRecord],
        *,
        now: datetime | None = None,
    ) -> list[BackfillOutcome]:
        """按队列顺序跑补抽任务，后面的任务能看到前面任务刚补出来的帧。

        「看得到」很重要：两个事件窗口重叠时，后一个任务必须把前一个补出来的帧算进
        去重口径，否则同一时刻会被补两次，帧表里留下两行同 image_id 的记录（Paimon
        主键 upsert 会盖掉一条，但成本账已经多算了一遍）。
        """
        pool: list[FrameRecord] = list(existing)
        outcomes: list[BackfillOutcome] = []
        for task in tasks:
            outcome = self.run_backfill_task(clip, task, pool, now=now)
            outcomes.append(outcome)
            pool.extend(outcome.created)
        return outcomes

    # ---- 内部 ----

    def _check_compliance(self, clip: ClipInput) -> str:
        """抽帧任务启动前的双脱敏校验。

        非严格模式下把「标记缺失」与「脱敏没做完」分别落成 unknown / rejected 两种
        状态——两者都拒绝放行，但湖里要能分得出该去修上游 join 还是去修合规链路。
        """
        marks = clip.compliance
        if self.strict_compliance:
            require_desensitized(marks, clip.data_id)
            return DesensitizationStatus.PASSED.value
        decision = check_desensitization(marks, clip.data_id)
        return decision.status.value

    def _build_cost(
        self,
        clip: ClipInput,
        *,
        routine_out: int,
        event_new: int,
        event_covered: int,
        inference_in: int,
        keyframe_out: int,
    ) -> PipelineCostReport:
        """按「每道闸门进多少出多少」结账。

        闸门二的产出按「窗口覆盖的帧数」计（新抽的 + 与常规抽帧重合的），
        而不是只按新增帧计——原文的 20/600 说的是窗口内的采样密度，
        和这些帧碰巧是不是已经被闸门一抽过无关。落湖增量另由 ``event_new`` 体现。
        """
        full = full_frame_count(clip.duration_sec, clip.camera_count)
        gate1 = build_gate_report(SamplingTier.ROUTINE, full, routine_out, self.cost_model)
        # 闸门二的分母是「窗口内的全量帧」：20 秒 × 30 fps = 600 张/路（原文数字）
        event_input = self._event_gate_input(clip, event_covered)
        gate2 = build_gate_report(SamplingTier.EVENT, event_input, event_covered, self.cost_model)
        # 闸门三的输入是「候选池」而不是全部落湖帧：原文四章末尾的向量化成本分级里，
        # 普通帧在进 VLM 之前已经先被抽样收窄过一道，账要记在这一层上。
        gate3 = build_gate_report(
            SamplingTier.INFERENCE, inference_in, keyframe_out, self.cost_model
        )
        return PipelineCostReport(
            full, (gate1, gate2, gate3), self.cost_model, stored_frames=routine_out + event_new
        )

    @staticmethod
    def _event_gate_input(clip: ClipInput, event_covered: int) -> int:
        """闸门二的输入帧数。

        原文给的对照是「20 秒变成 600 张全量帧」，即每个窗口每路 600 帧。
        这里按窗口实际覆盖的帧数反推覆盖了几个窗口，避免没有事件时分母凭空变大。
        窗口贴着 clip 边界会少几帧（原文说的是「约 20 帧」），所以向上取整。
        """
        if event_covered <= 0:
            return 0
        frames_per_window = K.EVENT_EXPECTED_FRAMES * clip.camera_count
        windows = max(1, -(-event_covered // frames_per_window))  # ceil
        return windows * K.EVENT_WINDOW_FULL_FRAMES * clip.camera_count


# --------------------------------------------------------------------------- 工具


def _make_frame(
    clip: ClipInput,
    camera_id: str,
    frame_index: int,
    *,
    tier: SamplingTier,
    interval_sec: float,
    run_id: str,
    algo_version: str,
) -> FrameRecord:
    """按 clip 上下文造一条帧记录（不含打分与事件上下文）。"""
    offset_ms = frame_index_to_offset_ms(frame_index)
    width, height = clip.camera_resolutions.get(camera_id, (None, None))
    return FrameRecord(
        image_id=build_image_id(clip.data_id, camera_id, frame_index),
        data_id=clip.data_id,
        camera_id=camera_id,
        camera_position=clip.camera_positions.get(camera_id, ""),
        frame_index=frame_index,
        frame_timestamp=clip.clip_start + timedelta(milliseconds=offset_ms),
        clip_offset_ms=offset_ms,
        run_id=run_id,
        project_code=clip.project_code,
        vehicle_code=clip.vehicle_code,
        gps_lat=clip.gps_lat,
        gps_lon=clip.gps_lon,
        file_path=clip.frame_path(camera_id, frame_index),
        image_width=width,
        image_height=height,
        sampling_tier=tier.value,
        sampling_interval_sec=interval_sec,
        algo_version=algo_version,
    )


def _attach_event_context(
    frame: FrameRecord, marker: EventMarker, win_start: datetime, win_end: datetime
) -> None:
    """把事件上下文附加到帧上。已有事件的帧不覆盖——先命中的事件优先。"""
    if frame.event_trigger_type:
        return
    frame.event_trigger_type = marker.trigger_type.value
    frame.event_time = marker.event_time
    frame.event_window_start = win_start
    frame.event_window_end = win_end
    if marker.parent_artifact_id:
        frame.parent_artifact_id = marker.parent_artifact_id


#: 便捷类型别名：调用方可以直接传一个 lambda 当信号源
SignalFn = Callable[[FrameRecord, ClipInput], FrameSignals]
