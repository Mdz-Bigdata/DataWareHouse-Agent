"""推理抽帧的打分与选帧：用打分代替随机选帧。

原文四章，逐字保留的要点：
  · "每个 clip 只选 1~5 张关键帧，选谁由打分决定，三个维度"
  · 图像清晰度——"模糊、过曝、遮挡的帧直接降权，对应帧表里的 frame_quality_score"
  · 目标丰富度——"画面里车辆、行人、交通设施越多，语义信息量越大"
  · 时间位置——"事件窗口中心、场景切换时刻的帧优先"
  · "frame_quality_score 作为帧表字段落湖，选帧逻辑调整时可以重算分数重新圈选，
     历史推理结果也能按当时的分数复盘"
  · 向量化成本分级（引上一篇）："规则命中、事件抽帧产出的帧优先进 VLM，普通帧抽样"

⚠️ 原文未明确，本项目设计（全部集中在 constants.py，可整体覆盖）：
  三个维度的权重（默认三等分）、目标丰富度的饱和点、时间位置的衰减尺度、
  「到底选几张」的综合分下限、关键帧之间的最小时间间距。
  原文只规定了维度与区间，没有规定这些参数的取值。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from . import constants as K
from .frames import EventTriggerType, FrameRecord, SamplingTier

__all__ = [
    "ScoreWeights",
    "FrameSignals",
    "FrameScore",
    "compute_clarity_score",
    "compute_object_richness_score",
    "compute_temporal_position_score",
    "score_frame",
    "score_frames",
    "select_keyframes",
    "vlm_priority",
]


def _clamp01(value: float) -> float:
    """把任意实数夹到 [0, 1]，打分域统一。"""
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """三个打分维度的权重。

    默认三等分（⚠️ 原文未明确，本项目设计：原文只列了三个维度，没给权重）。
    权重会在 ``normalized()`` 里归一化，调用方可以随手传 (2, 1, 1) 这种相对值。
    """

    clarity: float = float(K.SCORE_WEIGHT_CLARITY)
    object_richness: float = float(K.SCORE_WEIGHT_OBJECT_RICHNESS)
    temporal_position: float = float(K.SCORE_WEIGHT_TEMPORAL_POSITION)

    def __post_init__(self) -> None:
        for name in ("clarity", "object_richness", "temporal_position"):
            if getattr(self, name) < 0:
                raise ValueError(f"权重不能为负：{name}={getattr(self, name)}")
        if self.total <= 0:
            raise ValueError("三个维度的权重之和必须大于 0")

    @property
    def total(self) -> float:
        return self.clarity + self.object_richness + self.temporal_position

    def normalized(self) -> tuple[float, float, float]:
        t = self.total
        return (self.clarity / t, self.object_richness / t, self.temporal_position / t)


@dataclass(frozen=True, slots=True)
class FrameSignals:
    """打分所需的原始信号，由抽帧引擎在解码帧时采集。

    ⚠️ 原文未明确，本项目设计：原文只说 "模糊、过曝、遮挡" 三种劣化与
    "车辆、行人、交通设施" 三类目标，没给这些信号的度量方式。本项目约定：
    三个劣化信号都是 [0,1] 的「劣化程度」（0 = 完全没有该问题），
    三类目标都是画面内的实例计数。
    """

    #: 模糊程度（0=清晰，1=完全糊）。工程实现常用 Laplacian 方差归一化
    blur: float = 0.0
    #: 过曝程度（0=曝光正常，1=严重过曝）。常用高亮像素占比
    overexposure: float = 0.0
    #: 遮挡比例（0=无遮挡，1=全遮挡）。常用镜头污损/雨雾检测
    occlusion: float = 0.0
    #: 画面内车辆数
    vehicle_count: int = 0
    #: 画面内行人数
    pedestrian_count: int = 0
    #: 画面内交通设施数（信号灯、标志牌、锥桶等）
    traffic_facility_count: int = 0
    #: 该帧距离事件时刻的秒数（无事件时为 None）
    seconds_from_event: float | None = None
    #: 该帧是否命中场景切换时刻
    is_scene_change: bool = False


@dataclass(frozen=True, slots=True)
class FrameScore:
    """单帧的打分结果。三个维度分单独保留，保证「可配置、可回溯」。"""

    image_id: str
    clarity: float
    object_richness: float
    temporal_position: float
    total: float
    weights: ScoreWeights = field(default_factory=ScoreWeights)

    def apply_to(self, frame: FrameRecord) -> FrameRecord:
        """把分数写回帧记录（frame_quality_score 是原文点名的落湖字段）。"""
        frame.frame_quality_score = self.clarity
        frame.object_richness_score = self.object_richness
        frame.temporal_position_score = self.temporal_position
        frame.keyframe_score = self.total
        return frame


# --------------------------------------------------------------------------- 维度一


def compute_clarity_score(signals: FrameSignals) -> float:
    """维度一 · 图像清晰度：模糊、过曝、遮挡的帧直接降权。

    实现为乘性保留系数——三种劣化任意一种拉满，清晰度分即归零，符合原文
    「直接降权」的语气（而不是各扣一点的加权平均）。

    ⚠️ 原文未明确，本项目设计：三个劣化系数的权重默认都是 1.0
    （见 constants.CLARITY_*_PENALTY_WEIGHT），即系数就是劣化程度本身。

    Returns:
        [0, 1] 的清晰度分，落 frame_quality_score。
    """
    blur = _clamp01(signals.blur * K.CLARITY_BLUR_PENALTY_WEIGHT)
    over = _clamp01(signals.overexposure * K.CLARITY_EXPOSURE_PENALTY_WEIGHT)
    occl = _clamp01(signals.occlusion * K.CLARITY_OCCLUSION_PENALTY_WEIGHT)
    return _clamp01((1.0 - blur) * (1.0 - over) * (1.0 - occl))


# --------------------------------------------------------------------------- 维度二


def compute_object_richness_score(
    signals: FrameSignals,
    *,
    saturation_count: int = K.OBJECT_RICHNESS_SATURATION_COUNT,
) -> float:
    """维度二 · 目标丰富度：车辆、行人、交通设施越多，语义信息量越大。

    用饱和曲线而不是线性——目标数从 0 到 3 的信息增量远大于从 30 到 33。

    ⚠️ 原文未明确，本项目设计：原文没给饱和点，默认取 10 个目标
    （constants.OBJECT_RICHNESS_SATURATION_COUNT）；也没给三类目标的相对权重，
    本项目按「一个目标就是一个目标」等权计数，不偏袒任何一类。

    Args:
        signals: 三类目标的计数。
        saturation_count: 达到该目标数即接近满分。必须为正。

    Returns:
        [0, 1] 的丰富度分。
    """
    if saturation_count <= 0:
        raise ValueError(f"饱和点必须为正，收到 {saturation_count}")
    for name in ("vehicle_count", "pedestrian_count", "traffic_facility_count"):
        if getattr(signals, name) < 0:
            raise ValueError(f"目标计数不能为负：{name}={getattr(signals, name)}")
    total = signals.vehicle_count + signals.pedestrian_count + signals.traffic_facility_count
    # 1 - exp(-n/S)：n=S 时约 0.632，n=3S 时约 0.95，单调不饱和到死
    return _clamp01(1.0 - math.exp(-total / saturation_count))


# --------------------------------------------------------------------------- 维度三


def compute_temporal_position_score(
    signals: FrameSignals,
    *,
    decay_scale_seconds: float = K.TEMPORAL_DECAY_SCALE_SECONDS,
) -> float:
    """维度三 · 时间位置：事件窗口中心、场景切换时刻的帧优先。

    「事件窗口中心」= 事件时刻本身（原文事件窗口是围绕事件时刻取的前 15 后 5），
    距离越远分越低，按指数衰减。场景切换帧直接给满分——它是另一个独立的优先信号。

    ⚠️ 原文未明确，本项目设计：衰减尺度默认 5.0 秒
    （constants.TEMPORAL_DECAY_SCALE_SECONDS，与原文事件后窗口 5 秒同量级）；
    无事件、也非场景切换的普通帧给 0.0，即时间位置维度不给它加分也不扣分。

    Args:
        signals: 需要 ``seconds_from_event`` 或 ``is_scene_change``。
        decay_scale_seconds: 指数衰减尺度（秒），必须为正。

    Returns:
        [0, 1] 的时间位置分。
    """
    if decay_scale_seconds <= 0:
        raise ValueError(f"衰减尺度必须为正，收到 {decay_scale_seconds}")
    if signals.is_scene_change:
        return 1.0
    if signals.seconds_from_event is None:
        return 0.0
    return _clamp01(math.exp(-abs(signals.seconds_from_event) / decay_scale_seconds))


# --------------------------------------------------------------------------- 综合


def score_frame(
    frame: FrameRecord,
    signals: FrameSignals,
    *,
    weights: ScoreWeights | None = None,
) -> FrameScore:
    """对单帧打三个维度的分并加权汇总。

    Args:
        frame: 待打分的帧记录。
        signals: 该帧的原始信号。
        weights: 维度权重，默认三等分。

    Returns:
        FrameScore。调用 ``apply_to(frame)`` 可把分数写回帧记录落湖。
    """
    w = weights or ScoreWeights()
    clarity = compute_clarity_score(signals)
    richness = compute_object_richness_score(signals)
    temporal = compute_temporal_position_score(signals)
    wc, wr, wt = w.normalized()
    total = _clamp01(clarity * wc + richness * wr + temporal * wt)
    return FrameScore(frame.image_id, clarity, richness, temporal, total, w)


def score_frames(
    pairs: Iterable[tuple[FrameRecord, FrameSignals]],
    *,
    weights: ScoreWeights | None = None,
    write_back: bool = True,
) -> list[FrameScore]:
    """批量打分。``write_back=True`` 时同步把分数写回帧记录。"""
    w = weights or ScoreWeights()
    out: list[FrameScore] = []
    for frame, signals in pairs:
        s = score_frame(frame, signals, weights=w)
        if write_back:
            s.apply_to(frame)
        out.append(s)
    return out


# --------------------------------------------------------------------------- 选帧


def select_keyframes(
    frames: Sequence[FrameRecord],
    *,
    min_k: int = K.INFERENCE_MIN_KEYFRAMES,
    max_k: int = K.INFERENCE_MAX_KEYFRAMES,
    score_floor: float = K.KEYFRAME_SCORE_FLOOR,
    min_spacing_seconds: float = K.KEYFRAME_MIN_SPACING_SECONDS,
) -> list[FrameRecord]:
    """每个 clip 选 1~5 张关键帧（原文四章 "每 clip 打分选 1~5 关键帧"）。

    选帧规则（⚠️ 原文只规定了 1~5 的区间与三个打分维度，「到底选几张」
    与「相邻帧怎么去重」原文未明确，以下为本项目设计）：
      1. 按 keyframe_score 降序；
      2. 综合分 ≥ score_floor 的才有资格入选（默认 0.5，[0,1] 域的中位线）；
      3. 与已入选帧的时间间距 < min_spacing_seconds 的跳过——原文说
         "相邻帧高度相似"，送两张几乎一样的图进 VLM 是纯浪费；
      4. 数量裁剪到 [min_k, max_k]；若达标帧不足 min_k，用分数最高的帧补齐，
         保证每个进入 VLM 范围的 clip 至少有 1 张关键帧（原文下限是 1，不是 0）。

    Args:
        frames: 同一个 clip 的候选帧，必须已经打过分（keyframe_score 非 None）。
        min_k: 关键帧数下限，原文为 1。
        max_k: 关键帧数上限，原文为 5。
        score_floor: 入选的综合分下限。
        min_spacing_seconds: 两张关键帧之间的最小时间间距（秒）。

    Returns:
        按时间升序的关键帧列表，已把 ``is_keyframe`` 置 True。

        注意**不改动 sampling_tier**：推理抽帧不产新图，它是在闸门一、二已落湖的帧里
        挑（原文四章说的是「选帧」）。sampling_tier 保留「这帧是哪道闸门抽出来的」这一
        血缘事实，「是否被选进 VLM」由 is_keyframe 表达。两件事分开记，重算分数重新
        圈选时才不会把帧的来源冲掉。

    Raises:
        ValueError: 区间非法，或候选帧里有未打分的。
    """
    if min_k < 1:
        raise ValueError(f"关键帧下限不能小于 1（原文 1~5），收到 {min_k}")
    if max_k < min_k:
        raise ValueError(f"关键帧上限 {max_k} 小于下限 {min_k}")
    if not frames:
        return []

    for f in frames:
        if f.keyframe_score is None:
            raise ValueError(f"帧 {f.image_id} 尚未打分，无法选帧；请先调用 score_frames")

    ranked = sorted(
        frames,
        key=lambda f: (-(f.keyframe_score or 0.0), f.clip_offset_ms),
    )

    def _pick(candidates: Sequence[FrameRecord], chosen: list[FrameRecord]) -> None:
        spacing_ms = min_spacing_seconds * 1000.0
        for cand in candidates:
            if len(chosen) >= max_k:
                return
            if any(abs(cand.clip_offset_ms - c.clip_offset_ms) < spacing_ms for c in chosen):
                continue
            chosen.append(cand)

    selected: list[FrameRecord] = []
    _pick([f for f in ranked if (f.keyframe_score or 0.0) >= score_floor], selected)
    if len(selected) < min_k:
        # 兜底：达标帧不够，用剩下分数最高的补齐（间距约束在此放宽，否则可能补不满）
        remaining = [f for f in ranked if f not in selected]
        for cand in remaining:
            if len(selected) >= min_k:
                break
            selected.append(cand)

    selected.sort(key=lambda f: f.clip_offset_ms)
    for f in selected:
        f.is_keyframe = True
    return selected


# --------------------------------------------------------------------------- 成本分级


def vlm_priority(frame: FrameRecord) -> bool:
    """向量化/推理成本分级：该帧是否优先进 VLM。

    原文四章末尾引上一篇："规则命中、事件抽帧产出的帧优先进 VLM，普通帧抽样"。
    这里只判定「优先」与否；普通帧的抽样比例见
    constants.ORDINARY_FRAME_VLM_SAMPLE_RATIO（⚠️ 原文未明确，本项目设计 0.1）。

    Returns:
        True 表示该帧属于「优先」档：事件抽帧产出的帧，或命中挖掘规则的帧。
    """
    if frame.tier is SamplingTier.EVENT:
        return True
    return frame.event_trigger_type == EventTriggerType.RULE_HIT.value
