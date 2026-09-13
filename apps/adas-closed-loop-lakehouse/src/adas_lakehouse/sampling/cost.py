"""三道成本闸门的账：每道闸门的过滤比例、成本与收益。

原文二章立的论点：
  "抽帧的每一层策略，本质上是一道成本闸门。频率越高，覆盖越细，但存储、算力与
   下游推理成本同步放大。"
原文四章：
  "GPU 推理成本与送进去的图片数量成正比，送全量帧既不经济也无必要（相邻帧高度相似）。"

⚠️ 诚实声明：原文**没有给出任何耗时（秒/毫秒）或金额（元）数字**，只有定性论述。
因此本模块拒绝杜撰单价与耗时：
  · 唯一可信的计量是「帧数」，它可以由原文数字精确推出；
  · CostModel 里的所有单价默认都是 **1.0 相对单位**，真实单价由调用方按自家
    对象存储/GPU 报价注入；
  · 所有比例用 fractions.Fraction 精确表示（1/60、1/30 …），不做四舍五入。

比例的推导链路（全部基于原文原话）：
  闸门一 常规抽帧：2 秒 1 帧 ÷ 30 fps          = 1/60   （过滤掉 59/60）
  闸门二 事件抽帧：约 20 帧 ÷ 600 张全量帧      = 1/30   （过滤掉 29/30）
  闸门三 推理抽帧：1~5 关键帧 ÷ 每 clip 30 帧    = 1/30 ~ 1/6
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from . import constants as K
from .frames import SamplingTier

__all__ = [
    "CostModel",
    "GateCostReport",
    "PipelineCostReport",
    "full_frame_count",
    "routine_frame_count",
    "event_frame_count",
    "gate_keep_ratio",
    "build_gate_report",
    "estimate_pipeline_cost",
]


def full_frame_count(duration_seconds: float, camera_count: int = 1) -> int:
    """全量抽帧的帧数 = 30 fps × 时长 × 路数。

    30 来自原文引言 "一路摄像头 10 秒 30 帧就是 300 张图"。
    这是三道闸门的共同分母——闸门省下来的成本都相对它衡量。
    """
    if duration_seconds < 0:
        raise ValueError(f"时长不能为负，收到 {duration_seconds}")
    if camera_count < 1:
        raise ValueError(f"摄像头路数至少为 1，收到 {camera_count}")
    return int(K.NATIVE_VIDEO_FPS * duration_seconds * camera_count)


def routine_frame_count(duration_seconds: float, camera_count: int = 1) -> int:
    """闸门一产出帧数 = 时长 ÷ 2 秒 × 路数（原文 "默认 2 秒 1 帧"）。"""
    if duration_seconds < 0:
        raise ValueError(f"时长不能为负，收到 {duration_seconds}")
    if camera_count < 1:
        raise ValueError(f"摄像头路数至少为 1，收到 {camera_count}")
    per_camera = int(duration_seconds // K.ROUTINE_INTERVAL_SECONDS) * K.ROUTINE_FRAMES_PER_INTERVAL
    return per_camera * camera_count


def event_frame_count(event_count: int, camera_count: int = 1) -> int:
    """闸门二产出帧数 = 事件数 × 约 20 帧 × 路数。

    20 来自原文 "事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）"。
    """
    if event_count < 0:
        raise ValueError(f"事件数不能为负，收到 {event_count}")
    if camera_count < 1:
        raise ValueError(f"摄像头路数至少为 1，收到 {camera_count}")
    return event_count * K.EVENT_EXPECTED_FRAMES * camera_count


def gate_keep_ratio(tier: SamplingTier) -> Fraction | tuple[Fraction, Fraction]:
    """该闸门的保留比例（相对它自己的输入）。

    Returns:
        闸门一、二返回单个 Fraction；闸门三返回 (下限, 上限) 二元组，
        因为原文给的是 "1~5 关键帧" 这个区间而非定值。
    """
    if tier is SamplingTier.ROUTINE:
        return K.ROUTINE_KEEP_RATIO
    if tier is SamplingTier.EVENT:
        return K.EVENT_KEEP_RATIO
    return (K.INFERENCE_KEEP_RATIO_MIN, K.INFERENCE_KEEP_RATIO_MAX)


@dataclass(frozen=True, slots=True)
class CostModel:
    """单位成本。**所有默认值都是 1.0 相对单位，不是原文数字。**

    ⚠️ 原文未明确，本项目设计：原文对成本只有定性描述（"存储、算力与下游推理成本
    同步放大"、"GPU 推理成本与送进去的图片数量成正比"），没给任何单价。默认取
    1.0 让报表退化成纯帧数比较——这样即使没接财务口径，闸门的相对收益也成立。
    真实部署请按自家对象存储与 GPU 报价注入。

    Attributes:
        storage_per_frame: 每帧存储成本（相对单位）。
        decode_per_frame: 每帧解码/抽帧算力成本（相对单位）。
        vlm_per_frame: 每帧 VLM 推理成本（相对单位）。原文："与送进去的图片数量成正比"。
    """

    storage_per_frame: float = 1.0
    decode_per_frame: float = 1.0
    vlm_per_frame: float = 1.0
    #: 成本单位的名字，仅用于报表可读性
    unit: str = "相对单位"

    def __post_init__(self) -> None:
        for name in ("storage_per_frame", "decode_per_frame", "vlm_per_frame"):
            if getattr(self, name) < 0:
                raise ValueError(f"单位成本不能为负：{name}={getattr(self, name)}")


@dataclass(frozen=True, slots=True)
class GateCostReport:
    """一道闸门的账：进多少帧、出多少帧、过滤掉多少、花多少、换到什么。"""

    tier: SamplingTier
    input_frames: int
    output_frames: int
    cost_model: CostModel = field(default_factory=CostModel)

    @property
    def name_cn(self) -> str:
        return f"{self.tier.name_cn}（{self.tier.role}）"

    @property
    def keep_ratio(self) -> Fraction:
        """实际保留比例 = 出 / 进，精确分数。"""
        if self.input_frames == 0:
            return Fraction(0)
        return Fraction(self.output_frames, self.input_frames)

    @property
    def filtered_ratio(self) -> Fraction:
        """实际过滤比例 = 1 - 保留比例。这就是这道闸门「关掉」的成本。"""
        return Fraction(1) - self.keep_ratio

    @property
    def filtered_frames(self) -> int:
        return self.input_frames - self.output_frames

    @property
    def declared_keep_ratio(self) -> Fraction | tuple[Fraction, Fraction]:
        """原文数字推导出的应然保留比例，用于和实际值对账。"""
        return gate_keep_ratio(self.tier)

    def storage_cost(self) -> float:
        """本闸门产物的存储成本。"""
        return self.output_frames * self.cost_model.storage_per_frame

    def decode_cost(self) -> float:
        """本闸门的抽帧算力成本。按输入帧计——不解码就不知道该不该留。

        ⚠️ 原文未明确，本项目设计：原文没说算力按输入还是输出计。按输入计是
        保守口径（抽帧必须先解码候选帧），不会低估闸门自身的开销。
        """
        return self.input_frames * self.cost_model.decode_per_frame

    def vlm_cost(self) -> float:
        """下游 VLM 推理成本。只有推理抽帧的产物真正进 VLM。"""
        if self.tier is not SamplingTier.INFERENCE:
            return 0.0
        return self.output_frames * self.cost_model.vlm_per_frame

    def total_cost(self) -> float:
        return self.storage_cost() + self.decode_cost() + self.vlm_cost()

    def saved_vs_full(self, full_frames: int) -> int:
        """相对「全量抽帧」省下的帧数。"""
        return max(0, full_frames - self.output_frames)

    @property
    def benefit(self) -> str:
        """这道闸门换来什么——原文闸门表的「用途」列，逐字。"""
        return self.tier.purpose

    def describe(self) -> str:
        """人可读的一行账。"""
        return (
            f"{self.name_cn}: 触发条件={self.tier.trigger_condition}; "
            f"频率={self.tier.frequency}; "
            f"入 {self.input_frames} 帧 → 出 {self.output_frames} 帧 "
            f"(保留 {self.keep_ratio}, 过滤 {self.filtered_ratio}); "
            f"成本 {self.total_cost():g} {self.cost_model.unit}; "
            f"收益={self.benefit}"
        )


@dataclass(frozen=True, slots=True)
class PipelineCostReport:
    """三道闸门串联后的总账。"""

    full_frames: int
    gates: tuple[GateCostReport, ...]
    cost_model: CostModel = field(default_factory=CostModel)
    #: 真正落表的去重后帧数。不传则按闸门一 + 闸门二产出相加——
    #: 但闸门二的产出是「窗口覆盖帧数」，其中与闸门一重合的部分不会重复落表，
    #: 所以串联执行时由 SamplingPipeline 显式传入去重后的真实值。
    stored_frames: int | None = None

    @property
    def lake_frames(self) -> int:
        """真正落 dwd_mining_image_frame_detail 的帧数（去重后）。

        闸门三不产生新图片，它只是在已落湖的帧里打标 is_keyframe——
        原文四章的说法是 "选帧"，选的是存量帧，不是再抽一批。
        """
        if self.stored_frames is not None:
            return self.stored_frames
        return sum(
            g.output_frames
            for g in self.gates
            if g.tier in (SamplingTier.ROUTINE, SamplingTier.EVENT)
        )

    @property
    def vlm_frames(self) -> int:
        """真正进 VLM 的帧数 = 闸门三产出。"""
        return sum(g.output_frames for g in self.gates if g.tier is SamplingTier.INFERENCE)

    @property
    def overall_keep_ratio(self) -> Fraction:
        """落湖帧 ÷ 全量帧——三道闸门合起来把湖仓压到了几分之一。"""
        if self.full_frames == 0:
            return Fraction(0)
        return Fraction(self.lake_frames, self.full_frames)

    @property
    def vlm_keep_ratio(self) -> Fraction:
        """进 VLM 帧 ÷ 全量帧——最贵的那档压到了几分之一。"""
        if self.full_frames == 0:
            return Fraction(0)
        return Fraction(self.vlm_frames, self.full_frames)

    def total_cost(self) -> float:
        return sum(g.total_cost() for g in self.gates)

    def describe(self) -> str:
        """多行报表：逐闸门一行，末尾一行总账。"""
        lines = [
            f"全量抽帧基线: {self.full_frames} 帧 "
            f"（{K.NATIVE_VIDEO_FPS} fps，源自原文「10 秒 30 帧就是 300 张图」）"
        ]
        lines.extend(f"  闸门{i}. {g.describe()}" for i, g in enumerate(self.gates, start=1))
        lines.append(
            f"总账: 落湖 {self.lake_frames} 帧（全量的 {self.overall_keep_ratio}）, "
            f"进 VLM {self.vlm_frames} 帧（全量的 {self.vlm_keep_ratio}）, "
            f"合计成本 {self.total_cost():g} {self.cost_model.unit}"
        )
        return "\n".join(lines)


def build_gate_report(
    tier: SamplingTier,
    input_frames: int,
    output_frames: int,
    cost_model: CostModel | None = None,
) -> GateCostReport:
    """构造一道闸门的账，并做基本自洽校验。

    Raises:
        ValueError: 输出帧多于输入帧（闸门只能过滤，不能凭空造帧）。
    """
    if input_frames < 0 or output_frames < 0:
        raise ValueError("帧数不能为负")
    if output_frames > input_frames:
        raise ValueError(
            f"{tier.name_cn} 输出 {output_frames} 帧 > 输入 {input_frames} 帧：闸门只过滤不造帧"
        )
    return GateCostReport(tier, input_frames, output_frames, cost_model or CostModel())


def estimate_pipeline_cost(
    duration_seconds: float,
    camera_count: int = 1,
    event_count: int = 0,
    keyframes_per_camera: int = K.INFERENCE_MAX_KEYFRAMES,
    cost_model: CostModel | None = None,
) -> PipelineCostReport:
    """**不抽帧**，只按原文的三个频率算一份成本预估。

    闸门的意义是在花钱之前就知道要花多少：一个几百 clip 的采集项目要不要开事件抽帧、
    要不要把这批 clip 放进 VLM 推理范围，靠的是这份预估，而不是跑完之后看账单。
    与 :meth:`SamplingPipeline.run` 产出的实际账目口径一致，可直接对照。

    Args:
        duration_seconds: 单个 clip 的时长。
        camera_count: 多路摄像头路数。
        event_count: 该 clip 上规则引擎识别出的事件数（闸门二只对有事件的 clip 生效）。
        keyframes_per_camera: 每路预计选几张关键帧，须落在原文的 [1, 5] 区间。
        cost_model: 单位成本；默认全 1.0 相对单位（原文没给任何单价）。

    Returns:
        PipelineCostReport，三道闸门各一行。

    Raises:
        ValueError: 关键帧数不在原文的 1~5 区间内。
    """
    if not K.INFERENCE_MIN_KEYFRAMES <= keyframes_per_camera <= K.INFERENCE_MAX_KEYFRAMES:
        raise ValueError(
            f"每路关键帧数必须落在原文的 "
            f"[{K.INFERENCE_MIN_KEYFRAMES}, {K.INFERENCE_MAX_KEYFRAMES}] 区间，"
            f"收到 {keyframes_per_camera}"
        )
    model = cost_model or CostModel()
    full = full_frame_count(duration_seconds, camera_count)
    routine_out = routine_frame_count(duration_seconds, camera_count)
    event_out = event_frame_count(event_count, camera_count)
    # 闸门二的分母是「窗口内的全量帧」：每个事件每路 600 帧（原文 "20 秒变成 600 张全量帧"）
    event_in = event_count * K.EVENT_WINDOW_FULL_FRAMES * camera_count
    # 闸门三的输入是候选池：优先帧（事件帧）全进 + 普通帧按比例抽样
    ordinary = max(0, routine_out - event_out)
    inference_in = event_out + int(ordinary * K.ORDINARY_FRAME_VLM_SAMPLE_RATIO)
    keyframe_out = min(inference_in, keyframes_per_camera * camera_count)

    gates = (
        build_gate_report(SamplingTier.ROUTINE, full, routine_out, model),
        build_gate_report(SamplingTier.EVENT, event_in, event_out, model),
        build_gate_report(SamplingTier.INFERENCE, inference_in, keyframe_out, model),
    )
    # 落表增量不等于闸门二的产出：20 秒窗口里每 2 秒有一个时刻与常规抽帧的网格重合
    # （1 秒 1 帧 vs 2 秒 1 帧），重合的那 20/2 = 10 个时刻共用同一个 image_id，
    # 主键 upsert 后只有一行。所以每个事件每路真正新增的是 20 - 10 = 10 帧。
    overlap_per_event_per_camera = K.EVENT_WINDOW_SECONDS // K.ROUTINE_INTERVAL_SECONDS
    new_per_event_per_camera = max(0, K.EVENT_EXPECTED_FRAMES - overlap_per_event_per_camera)
    event_new = event_count * camera_count * new_per_event_per_camera
    return PipelineCostReport(full, gates, model, stored_frames=routine_out + event_new)
