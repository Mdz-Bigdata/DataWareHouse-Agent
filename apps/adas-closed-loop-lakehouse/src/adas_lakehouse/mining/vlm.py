"""VLM 推理挖掘引擎：补规则写不出来的语义级长尾标签。

它是三级漏斗的第二层。原文（[S3-04] 四）把三层的分工写得很清楚：

    规则先验粗筛 → 模型不确定性细筛 → 检索相似性扩散。
    规则引擎以几乎为零的边际成本扫完全量元数据，把「疑似高价值」的候选圈出来；
    VLM 推理再对候选里信息密度最高的帧做语义确认（下篇讲）；
    语义检索最后把相似场景扩散成完整数据集。

以及它存在的理由（[S3-04] 结尾）：

    规则引擎再强，也有够不着的地方：「施工区锥桶摆放混乱」「行人撑着花伞」
    这类语义级场景，结构化条件写不出来——这正是大模型推理挖掘的领地。
    下篇讲 VLM 推理引擎：选帧打分、双输出（标签 + caption）、Ray + GPU 调度与
    断点续跑，长尾场景的标签它来补。

--------------------------------------------------------------------------------
⚠️ 原文取得情况
--------------------------------------------------------------------------------
[S3-05]《VLM 推理挖掘：多模态大模型如何补足长尾场景标签》**本项目未取得原文**，
上面那段预告是关于它的全部一手信息。所以本模块的口径只能锚在已取得的四篇上：

==============  ================================================================
本模块的落点      原文出处
==============  ================================================================
选帧             [S3-02] 二、三道闸门表「推理抽帧」行：「进入 VLM 推理范围的 clip｜
                每 clip 打分选 1~5 关键帧｜按清晰度/目标丰富度/时间位置选帧，
                控制推理成本」；[S3-04] 四：「对候选里信息密度最高的帧做语义确认」
双输出           [S3-04] 结尾预告：「双输出（标签 + caption）」；
                [S3-01] 一：「多模态大模型做语义级标签与关键说明（caption）生成」
落哪张表         [S3-03] 二：「VLM 生成的关键说明（caption）以 tag_category=CAPTION
                的特殊标签写入图片标签表，与结构化标签同条记录口径并存，
                同时冗余一份到向量表」
不自建写入逻辑    [S3-04] 二：「所有命中统一经标签服务打标……不需要规则引擎自建一套
                标签写入逻辑」——推理引擎同理，见 :class:`ModelTagSink`
血缘             [S3-03] 二③：「每条标签携带 tag_source、rule_id / model_name /
                model_version、confidence、infer_job_id」
出口门禁         [S3-03] 四：「支持人工审核修正，审核结论回写 review_status /
                review_operator；未审核标签不得进入训练集圈选」
Ray + 断点续跑   [S3-01] 五：「GPU 推理调度选 Ray + vLLM（Triton 适合单模型服务化，
                但缺任务编排与断点续跑）」
==============  ================================================================

**凡是这四篇没写的（批大小、重试次数、退避秒数、提示词），一律在本模块里标
「⚠️ 原文未明确，本项目设计」，绝不放进 constants.py 冒充原文。**

--------------------------------------------------------------------------------
职责边界
--------------------------------------------------------------------------------
本模块**不做**三件事，每一件都有明确的归属方：

1. **不调度 GPU。** 分时错峰、优先级队列、抢占归 GPU 池
   （``dataplane.gpu``，原文 [S3-01] 五）。本引擎只产出可被调度的任务描述，
   见 :meth:`VlmInferenceEngine.describe_gpu_task`。
2. **不写标签表。** 字典映射、别名归一、幂等去重、候选池审核全在统一标签服务里
   （[S3-03] 二）。本引擎只把双输出递过去，拿回「实际写入多少条」。
3. **不选关键帧。** 选帧打分是抽帧引擎闸门三的活（``sampling``，[S3-02] 二），
   产物是 ``dwd_mining_image_frame_detail.is_keyframe`` / ``keyframe_score``。
   本引擎只**读**它，按分数取候选——两个引擎经湖仓表解耦，谁也不阻塞谁（[S3-01] 三）。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any, Protocol, runtime_checkable

from ..domains import Layer
from ..ids import ArtifactStatus, new_run_id
from ._sqlfmt import ident, join_predicates, literal
from .backends import BackendError, SqlBackend
from .constants import (
    INFERENCE_MAX_KEYFRAMES,
    INFERENCE_MIN_KEYFRAMES,
    VLM_CAPTION_TAG_CATEGORY,
    VLM_INFER_ENGINE,
    VLM_INFER_SERVING_RUNTIME,
    VLM_LONG_TAIL_EXAMPLES,
    VLM_OUTPUT_KINDS,
    VLM_REQUIRED_LINEAGE_FIELDS,
    VLM_TAG_SOURCE,
    VLM_TASK_TYPE,
)
from .tables import (
    DWD_MINING_IMAGE_FRAME_DETAIL,
    DWD_MINING_IMAGE_TAG_DETAIL,
    DWD_MINING_IMAGE_VECTOR_DETAIL,
    DWD_MINING_TASK_DETAIL,
    IMAGE_TAG_WRITE_COLUMNS,
    KEYFRAME_READ_COLUMNS,
    VLM_TASK_WRITE_COLUMNS,
    qualified,
)
from .watermark import Watermark, incremental_predicate, watermark_column

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INFER_BATCH_SIZE",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_CANDIDATE_LIMIT",
    "DESENSITIZE_PASSED",
    "REVIEW_PENDING",
    "INFER_ALGO_VERSION",
    "VlmInferError",
    "VlmOutputError",
    "InferCandidate",
    "VlmTag",
    "VlmOutput",
    "VlmClient",
    "EchoVlmClient",
    "InferBatch",
    "plan_batches",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "JsonFileCheckpointStore",
    "ImageTagWriteRequest",
    "ModelTagSink",
    "InMemoryModelTagSink",
    "HttpModelTagSink",
    "CaptionVectorSink",
    "InMemoryCaptionVectorSink",
    "SqlCaptionVectorSink",
    "InferTaskStatus",
    "VlmRunRecord",
    "VlmInferReport",
    "VlmInferenceEngine",
    "build_prompt",
]


# --------------------------------------------------------------------------- 本项目设计的参数

#: 一个推理批次送多少张图。
#: ⚠️ 原文未明确，本项目设计：原文只说「Ray + vLLM」，没给批大小。取 32 的理由是
#: 它同时是**断点粒度**——批太大则一次失败白烧的 GPU 秒太多，批太小则调度开销压过收益。
#: 部署时按显存与图片分辨率调，改这里一处即可。
DEFAULT_INFER_BATCH_SIZE = 32

#: 单个批次的最大尝试次数（含首次）。
#: ⚠️ 原文未明确，本项目设计：原文只说选 Ray 是为了「任务编排与断点续跑」，
#: 没给重试策略。取 3 次；三次都失败就把这一批标记为失败**并继续跑下一批**——
#: 一批坏图不该让整个推理作业前功尽弃，这正是「断点续跑」要的行为。
DEFAULT_MAX_ATTEMPTS = 3

#: 一次作业最多取多少张候选帧。
#: ⚠️ 原文未明确，本项目设计：GPU 预算的闸门。原文（[S3-04] 四）只说「对候选里
#: 信息密度最高的帧做语义确认」，「多少张」由 GPU 预算决定，不是原文常量。
DEFAULT_CANDIDATE_LIMIT = 10_000

#: 允许送进模型的脱敏状态。registry 里 dwd_mining_image_frame_detail.desensitize_status
#: 的注释写死了取值域 passed/pending/rejected，且注明「未脱敏一律拒绝抽帧」。
#: 抽帧侧已经拦过一道，这里再拦一道：合规红线在消费侧必须再设一道闸。
DESENSITIZE_PASSED = "passed"

#: 模型标签写出去时的审核状态。
#: [S3-03] 四原文：「未审核标签不得进入训练集圈选……模型产出永远先过审再上岗」。
#: registry 里 dwd_mining_image_tag_detail.review_status 的取值域是
#: unreviewed(pending)/approved/rejected/corrected。本引擎恒写待审，
#: **不提供任何跳过审核的开关**——留个 auto_approve 参数就等于把这道闸拆了。
REVIEW_PENDING = "pending"

#: artifact_id 的算法版本段。⚠️ 原文未明确，本项目设计。
INFER_ALGO_VERSION = "v1"

#: 推理任务的执行模式。推理不是 T+1 批也不是 Flink 准实时，而是按优先级抢 GPU 的
#: 队列作业（[S3-01] 三：「推理引擎按优先级抢 GPU」）。registry 里
#: dwd_mining_task_detail.exec_mode 的取值域注释指向 mining.rules.ExecutionMode，
#: 而那两个取值都不适用——所以这里写 batch_t_plus_1 会是撒谎。
#: ⚠️ 原文未明确，本项目设计：另起 ``gpu_queue`` 一值，并在收口阶段提请把它补进
#: registry 的 exec_mode 注释取值域（见模块 docstring 末尾的「交收口阶段」清单）。
INFER_EXEC_MODE = "gpu_queue"


class VlmInferError(RuntimeError):
    """推理调用失败。批次级捕获 → 重试 → 超限则记账，不拖垮整个作业。"""


class VlmOutputError(ValueError):
    """模型回来的东西不成立（缺标签、缺 caption、置信度越界……）。

    与 :class:`VlmInferError` 分开是刻意的：调用失败可以重试，
    **输出不合格重试多半还是不合格**，应该直接判废并计入 rejected。
    """


# --------------------------------------------------------------------------- 候选帧


@dataclass(frozen=True, slots=True)
class InferCandidate:
    """一张送进 VLM 的候选帧。

    来源是抽帧引擎闸门三的产物（[S3-02] 二「推理抽帧」行：「每 clip 打分选
    1~5 关键帧｜按清晰度/目标丰富度/时间位置选帧，控制推理成本」）。
    本引擎只读不写——选帧是抽帧引擎的活。

    Attributes:
        image_id: 图片级 ID，图片标签表的主键之一。
        data_id: 所属 clip 的终身锚点。
        image_object_key: 图片在对象存储里的 key，就是「送什么」的那个「什么」。
        keyframe_score: 三维加权综合分，候选排序用（「信息密度最高」的量化）。
        frame_quality_score / object_richness_score / temporal_position_score:
            选帧三维分项。原文要求选帧可重算复盘，所以分项一并带上，
            推理侧不重算，只做记录与排障。
        parent_artifact_id: 被打标的抽帧图片产物 ID，写进标签行做血缘父。
        desensitize_status: 脱敏状态，必须是 ``passed``。
        rule_id / rule_version: 把这张图圈进推理范围的规则——漏斗第一层的血缘，
            第二层不能让它断掉。
    """

    image_id: str
    data_id: str
    image_object_key: str = ""
    keyframe_score: float = 0.0
    frame_quality_score: float = 0.0
    object_richness_score: float = 0.0
    temporal_position_score: float = 0.0
    parent_artifact_id: str = ""
    desensitize_status: str = DESENSITIZE_PASSED
    camera_id: str = ""
    project_code: str = ""
    vehicle_code: str = ""
    frame_timestamp: datetime | None = None
    rule_id: str = ""
    rule_version: int = 0

    def __post_init__(self) -> None:
        if not self.image_id:
            raise VlmOutputError("候选帧缺少 image_id——图片标签表的主键之一，缺了写不进去")
        if not self.data_id:
            raise VlmOutputError(
                f"候选帧 {self.image_id} 缺少 data_id——"
                "「Badcase 图片 → 原始 clip」靠它做一次主键查询，不能空"
            )

    @property
    def is_sendable(self) -> bool:
        """能不能送进模型：脱敏未通过的一律不送。

        抽帧侧已经拦过一道（registry 该列注释：「未脱敏一律拒绝抽帧」），
        这里是消费侧的第二道。合规红线上「上游拦过了」不是放行的理由。
        """
        return self.desensitize_status == DESENSITIZE_PASSED and bool(self.image_object_key)

    @classmethod
    def from_row(
        cls, row: dict[str, Any], *, rule_id: str = "", rule_version: int = 0
    ) -> InferCandidate:
        """从 :data:`~adas_lakehouse.mining.tables.KEYFRAME_READ_COLUMNS` 的一行还原。

        Raises:
            VlmOutputError: 缺主键列。
        """
        return cls(
            image_id=str(row.get("image_id") or ""),
            data_id=str(row.get("data_id") or ""),
            image_object_key=str(row.get("image_object_key") or ""),
            keyframe_score=_as_float(row.get("keyframe_score")) or 0.0,
            frame_quality_score=_as_float(row.get("frame_quality_score")) or 0.0,
            object_richness_score=_as_float(row.get("object_richness_score")) or 0.0,
            temporal_position_score=_as_float(row.get("temporal_position_score")) or 0.0,
            # 抽帧产物的 artifact_id 就是标签行的血缘父产物
            parent_artifact_id=str(row.get("artifact_id") or ""),
            desensitize_status=str(row.get("desensitize_status") or DESENSITIZE_PASSED),
            camera_id=str(row.get("camera_id") or ""),
            project_code=str(row.get("project_code") or ""),
            vehicle_code=str(row.get("vehicle_code") or ""),
            frame_timestamp=_as_datetime(row.get("frame_timestamp")),
            rule_id=rule_id,
            rule_version=rule_version,
        )


# --------------------------------------------------------------------------- 双输出


@dataclass(frozen=True, slots=True)
class VlmTag:
    """模型吐出来的一条语义标签（原始写法，未过字典）。

    ``raw_tag`` 刻意保留模型的原话不做归一：归一是统一标签服务的职权
    （[S3-03] 二①字典映射 + 别名归一），这里先归一就等于在服务之外又造了一套口径。
    """

    raw_tag: str
    confidence: float

    def __post_init__(self) -> None:
        if not self.raw_tag.strip():
            raise VlmOutputError("模型标签的文本不能为空")
        if not 0.0 <= self.confidence <= 1.0:
            raise VlmOutputError(
                f"置信度需在 [0, 1]，收到 {self.confidence}——"
                "它要落 dwd_mining_image_tag_detail.confidence，越界值会污染下游筛选"
            )


@dataclass(frozen=True, slots=True)
class VlmOutput:
    """一张图的推理产出——**双输出**，两项缺一不可。

    [S3-04] 结尾预告逐字：「双输出（标签 + caption）」；
    [S3-01] 一：「多模态大模型做语义级标签与关键说明（caption）生成」。

    为什么把「缺一不可」做成硬校验而不是宽容降级：只回标签就丢了语义检索的那一半
    （caption 要冗余进向量表做以文搜图，[S3-03] 二），只回 caption 就没有结构化过滤
    的那一半。任何一半缺失，这条产出对下游都只剩半个用处，与其静默写半条，
    不如判废并计入 rejected，让覆盖度指标看得见。
    """

    image_id: str
    tags: tuple[VlmTag, ...]
    caption: str

    def __post_init__(self) -> None:
        if not self.image_id:
            raise VlmOutputError("推理产出缺少 image_id")
        if not self.tags:
            raise VlmOutputError(
                f"图片 {self.image_id} 的推理产出没有标签——"
                f"原文要求双输出{VLM_OUTPUT_KINDS}，缺一不可"
            )
        if not self.caption.strip():
            raise VlmOutputError(
                f"图片 {self.image_id} 的推理产出没有 caption——"
                f"原文要求双输出{VLM_OUTPUT_KINDS}，缺一不可；"
                "caption 还要冗余进向量表供以文搜图（[S3-03] 二）"
            )

    @property
    def top_confidence(self) -> float:
        return max(t.confidence for t in self.tags)


class VlmClient(ABC):
    """多模态大模型的调用接口。

    实现方可以是 Ray + vLLM 集群（[S3-01] 五的选型）、也可以是任何 HTTP 推理服务。
    本模块不 import ray / vllm——与 :mod:`adas_lakehouse.mining.backends` 同一条
    硬约束：导入本模块永远不能炸。
    """

    #: 模型名与版本，落标签行的血缘列（[S3-03] 二③）
    model_name: str = ""
    model_version: str = ""

    @abstractmethod
    def infer_batch(self, candidates: Sequence[InferCandidate], *, prompt: str) -> list[VlmOutput]:
        """对一批图做推理，返回双输出。

        Args:
            candidates: 本批候选帧（已过脱敏闸）。
            prompt: 提示词，见 :func:`build_prompt`。

        Returns:
            :class:`VlmOutput` 列表。**允许比入参短**——模型对某些图给不出合格产出时
            可以少回；少回的那些由引擎计入 rejected，不是错误。

        Raises:
            VlmInferError: 调用失败（网络、GPU OOM、服务不可用）。这类错误可重试。
        """


@dataclass(slots=True)
class EchoVlmClient(VlmClient):
    """本地假客户端：不碰 GPU，产出形状与真实推理一致。

    它不是玩具，承担两件正事：一是让整条链路（选帧 → 批次 → 断点续跑 → 双输出 →
    标签服务 → 执行追溯）在什么都没装的环境里可运行、可单测；二是演示「长尾场景」
    到底长什么样——默认吐的就是原文点名的那两个
    （:data:`~adas_lakehouse.mining.constants.VLM_LONG_TAIL_EXAMPLES`：
    「施工区锥桶摆放混乱」「行人撑着花伞」）。

    Attributes:
        fail_batches: 指定哪些 batch_index 要抛 :class:`VlmInferError`，
            用来验证重试与断点续跑。
        max_failures_per_batch: 每个指定批次失败多少次后转为成功——
            用来验证「重试之后能成功」而不只是「重试了」。
    """

    model_name: str = "echo-vlm"
    model_version: str = "v0"
    tags: tuple[str, ...] = VLM_LONG_TAIL_EXAMPLES
    confidence: float = 0.8
    fail_batches: frozenset[int] = frozenset()
    max_failures_per_batch: int = 10**9
    #: 每个批次已失败次数，供 max_failures_per_batch 判定
    failure_counts: dict[int, int] = field(default_factory=dict)
    #: 实际发起过推理的批次序号，断点续跑的断言靠它
    inferred_batches: list[int] = field(default_factory=list)
    _current_batch: int = field(default=-1, init=False, repr=False)

    def note_batch(self, batch_index: int) -> None:
        """引擎在调用前告知当前批次序号（只有假客户端需要知道）。"""
        self._current_batch = batch_index

    def infer_batch(self, candidates: Sequence[InferCandidate], *, prompt: str) -> list[VlmOutput]:
        idx = self._current_batch
        if idx in self.fail_batches:
            seen = self.failure_counts.get(idx, 0)
            if seen < self.max_failures_per_batch:
                self.failure_counts[idx] = seen + 1
                raise VlmInferError(f"注入的推理失败：batch {idx}（第 {seen + 1} 次）")
        self.inferred_batches.append(idx)
        return [
            VlmOutput(
                image_id=c.image_id,
                tags=tuple(VlmTag(t, self.confidence) for t in self.tags),
                caption=f"{self.tags[0]}；画面来自 {c.camera_id or '未知视角'}",
            )
            for c in candidates
        ]


def build_prompt(long_tail_scenes: Sequence[str] = VLM_LONG_TAIL_EXAMPLES) -> str:
    """拼提示词——「送什么」里除图片之外的那一半。

    ⚠️ 原文未明确，本项目设计：原文（[S3-05]）本项目未取得，没有任何提示词信息。
    这里唯一有原文依据的是**场景清单**：[S3-04] 结尾点名「施工区锥桶摆放混乱」
    「行人撑着花伞」这类「结构化条件写不出来」的语义级场景，本函数把它们作为
    示例列进提示词，其余措辞为本项目设计。

    提示词把「双输出」写成硬要求，与 :class:`VlmOutput` 的校验一一对应——
    要求与校验对不上，就会出现「模型照做了但我们判废」或者「模型没照做但我们放行」。
    """
    scenes = "、".join(f"「{s}」" for s in long_tail_scenes)
    return (
        "你是自动驾驶数据挖掘的视觉分析助手。请观察这张车载摄像头图片，"
        f"给出两项产出（缺一不可）：{VLM_OUTPUT_KINDS[0]}与 {VLM_OUTPUT_KINDS[1]}。\n"
        f"1. {VLM_OUTPUT_KINDS[0]}：列出画面里成立的语义级场景标签，每条附 0-1 的置信度。"
        f"重点关注结构化规则写不出来的长尾场景，例如 {scenes}。\n"
        f"2. {VLM_OUTPUT_KINDS[1]}：一句话描述画面里对自动驾驶决策有影响的关键事实。\n"
        '以 JSON 返回：{"tags": [{"tag": "...", "confidence": 0.0}], "caption": "..."}'
    )


# --------------------------------------------------------------------------- 批次与断点


@dataclass(frozen=True, slots=True)
class InferBatch:
    """一个推理批次：任务编排的最小单位，**也是断点续跑的断点粒度**。

    两件事共用一个粒度是有意的：断点如果比批次细，就得在批中间保存半个批的状态；
    如果比批次粗，一次失败要重跑的 GPU 秒就成倍上去。
    """

    job_id: str
    batch_index: int
    candidates: tuple[InferCandidate, ...]

    @property
    def size(self) -> int:
        return len(self.candidates)

    @property
    def data_ids(self) -> tuple[str, ...]:
        """本批覆盖的 clip 集合（去重保序）。执行追溯的 hit_data_count 用它。"""
        seen: dict[str, None] = {}
        for c in self.candidates:
            seen.setdefault(c.data_id, None)
        return tuple(seen)


def plan_batches(
    candidates: Sequence[InferCandidate],
    *,
    job_id: str,
    batch_size: int = DEFAULT_INFER_BATCH_SIZE,
) -> tuple[InferBatch, ...]:
    """把候选切成有序批次——原文选 Ray 换来的「任务编排」那一半。

    候选**按 keyframe_score 降序**排：原文（[S3-04] 四）说 VLM 是对「候选里信息密度
    最高的帧做语义确认」，而信息密度的量化就是抽帧侧算好的 keyframe_score
    （[S3-02] 二：按清晰度/目标丰富度/时间位置选帧）。先跑高分批次的好处很实在——
    GPU 预算中途耗尽时，已经花掉的那部分买到的是最值钱的帧。

    Args:
        candidates: 候选帧。脱敏未过闸的会被剔除（合规红线的第二道闸）。
        job_id: 推理作业 ID，同时是断点的 key 与标签血缘的 infer_job_id。
        batch_size: 批大小。

    Returns:
        有序批次元组，batch_index 从 0 起连续。

    Raises:
        ValueError: batch_size 非正。
    """
    if batch_size < 1:
        raise ValueError(f"batch_size 必须为正，收到 {batch_size}")
    sendable = [c for c in candidates if c.is_sendable]
    dropped = len(candidates) - len(sendable)
    if dropped:
        logger.warning(
            "作业 %s 有 %d 张候选帧未过脱敏闸或缺对象存储 key，不送进模型（合规红线）",
            job_id,
            dropped,
        )
    ordered = sorted(sendable, key=lambda c: (-c.keyframe_score, c.image_id))
    return tuple(
        InferBatch(job_id=job_id, batch_index=i, candidates=tuple(ordered[s : s + batch_size]))
        for i, s in enumerate(range(0, len(ordered), batch_size))
    )


@runtime_checkable
class CheckpointStore(Protocol):
    """断点存储——原文选 Ray 换来的「断点续跑」那一半。

    [S3-01] 五原文：「GPU 推理调度选 Ray + vLLM（Triton 适合单模型服务化，
    但缺任务编排与断点续跑）」。既然是为这两样能力放弃的 Triton，
    它们就必须真的实现，否则那个选型是白选的。

    断点存在**控制面**（[S3-01] 四：「控制面……存任务配置与执行状态」），
    不占湖仓：断点丢了最多重跑一批，不影响主数据。
    """

    def completed(self, job_id: str) -> set[int]:
        """该作业已完成的批次序号。"""
        ...

    def mark_done(self, job_id: str, batch_index: int) -> None:
        """标记一个批次完成。必须在**结果落库之后**才调。"""
        ...

    def clear(self, job_id: str) -> None:
        """清空该作业的断点（重刷时用）。"""
        ...


@dataclass(slots=True)
class InMemoryCheckpointStore(CheckpointStore):
    """进程内断点，单测与单进程干跑用。进程一死断点就没了，别用在生产。"""

    _done: dict[str, set[int]] = field(default_factory=dict)

    def completed(self, job_id: str) -> set[int]:
        return set(self._done.get(job_id, ()))

    def mark_done(self, job_id: str, batch_index: int) -> None:
        self._done.setdefault(job_id, set()).add(batch_index)

    def clear(self, job_id: str) -> None:
        self._done.pop(job_id, None)


class JsonFileCheckpointStore(CheckpointStore):
    """落本地 JSON 文件的断点，进程重启后还在。

    写入走「临时文件 + 原子 rename」：推理作业被抢占（原文 [S3-01] 五：「峰值期低优
    任务可被抢占」）时进程可能在任意时刻被杀，半截的断点文件比没有断点更糟——
    它会让续跑跳过其实没跑完的批次，那批图就永久没有标签，而且**没人会发现**。

    与 :mod:`adas_lakehouse.mining.watermark` 的 JsonFileWatermarkStore 同一套写法，
    刻意保持一致：平台运行态的持久化只该有一种模式。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    def _load_all(self) -> dict[str, list[int]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(
                "断点文件 %s 读取失败，按「无断点」处理（会全量重跑）: %s", self._path, exc
            )
            return {}
        return {k: list(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _dump_all(self, data: dict[str, list[int]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self._path)  # 原子替换
        except BaseException:
            with suppress_errors():
                os.unlink(tmp)
            raise

    def completed(self, job_id: str) -> set[int]:
        with self._lock:
            return set(self._load_all().get(job_id, ()))

    def mark_done(self, job_id: str, batch_index: int) -> None:
        with self._lock:
            data = self._load_all()
            done = set(data.get(job_id, ()))
            done.add(batch_index)
            data[job_id] = sorted(done)
            self._dump_all(data)

    def clear(self, job_id: str) -> None:
        with self._lock:
            data = self._load_all()
            if data.pop(job_id, None) is not None:
                self._dump_all(data)


class suppress_errors:  # noqa: N801 - 与 contextlib.suppress 同形，故用小写
    """吞掉清理阶段的 OSError。清理失败不该覆盖掉真正的异常。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


# --------------------------------------------------------------------------- 标签出口


@dataclass(frozen=True, slots=True)
class ImageTagWriteRequest:
    """递给统一标签服务的一条图片级标签（结构化标签或 caption）。

    **本引擎不写标签表。** [S3-04] 二把话说死了：「所有命中统一经标签服务打标……
    不需要规则引擎自建一套标签写入逻辑」——推理引擎同理。字典映射、别名归一、
    幂等去重、候选池审核全在服务侧，本类只负责把双输出按 registry 的列名递过去。

    payload 的键集合在 import 期就对着
    :data:`~adas_lakehouse.mining.tables.IMAGE_TAG_WRITE_COLUMNS` 校验过（见本模块末尾的
    断言）——键名与 registry 分叉这种错在 Python 侧完全静默，必须在 import 期拦住。
    """

    image_id: str
    data_id: str
    raw_tag: str
    tag_category: str
    infer_job_id: str
    run_id: str
    model_name: str
    model_version: str
    confidence: float | None = None
    caption_text: str = ""
    rule_id: str = ""
    rule_version: int = 0
    parent_artifact_id: str = ""
    camera_id: str = ""
    project_code: str = ""
    vehicle_code: str = ""
    tagged_at: datetime | None = None

    @property
    def is_caption(self) -> bool:
        return self.tag_category == VLM_CAPTION_TAG_CATEGORY

    def to_payload(self) -> dict[str, Any]:
        """渲染成标签服务的入参，键名逐字用 registry 的列名。"""
        return {
            "image_id": self.image_id,
            # 归一前的原始写法先占位 tag_id，字典映射由标签服务做；
            # 未命中字典的会被服务打进候选池待审（[S3-03] 二②），不会直接入库。
            "tag_id": self.raw_tag,
            "tag_source": VLM_TAG_SOURCE,
            "data_id": self.data_id,
            "tag_category": self.tag_category,
            "caption_text": self.caption_text,
            "confidence": self.confidence,
            "rule_id": self.rule_id,
            "rule_version": str(self.rule_version) if self.rule_version else "",
            "model_name": self.model_name,
            "model_version": self.model_version,
            "infer_job_id": self.infer_job_id,
            "run_id": self.run_id,
            "parent_artifact_id": self.parent_artifact_id,
            "camera_id": self.camera_id,
            "source_raw_tag": self.raw_tag,
            # [S3-03] 四：未审核标签不得进入训练集圈选，模型产出永远先过审再上岗
            "review_status": REVIEW_PENDING,
            "project_code": self.project_code,
            "vehicle_code": self.vehicle_code,
            "first_tag_time": self.tagged_at,
        }


class ModelTagSink(ABC):
    """统一标签服务的图片级入口。规则侧的对应物是 ``backends.TagService``。"""

    @abstractmethod
    def write_tags(self, requests: Sequence[ImageTagWriteRequest]) -> int:
        """批量打标，返回服务侧去重后实际写入的条数（回填执行追溯的「写入标签量」）。"""


@dataclass(slots=True)
class InMemoryModelTagSink(ModelTagSink):
    """进程内标签服务，dry-run 与单测用。

    自带去重，口径对齐 registry 的联合主键 ``(image_id, tag_id, tag_source)``——
    [S3-03] 二②：「标签事实表是 Paimon 主键表，联合主键 Upsert——重复写入无副作用，
    任务重跑不会产生重复标签」。断点续跑必然带来重复写，这条去重语义是它安全的前提。
    """

    written: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)

    def write_tags(self, requests: Sequence[ImageTagWriteRequest]) -> int:
        new = 0
        for req in requests:
            payload = req.to_payload()
            key = (payload["image_id"], payload["tag_id"], payload["tag_source"])
            if key not in self.written:
                new += 1
            self.written[key] = payload
        return new

    @property
    def captions(self) -> list[dict[str, Any]]:
        return [p for p in self.written.values() if p["tag_category"] == VLM_CAPTION_TAG_CATEGORY]


class HttpModelTagSink(ModelTagSink):
    """经 OpenAPI 网关调用统一标签服务的图片级批量写入。

    ⚠️ 原文未明确，本项目设计：[S3-01] 六给了四组接口的代表路径，但没给
    「批量写入图片标签」的具体路径。默认 ``/api/v1/tags/images/bulk-write``，可覆盖。
    幂等键的做法与 ``backends.HttpTagService`` 一致（[S3-01] 六：幂等键防重复提交）。
    """

    name = "http-model-tag-sink"
    DEFAULT_PATH = "/api/v1/tags/images/bulk-write"

    def __init__(
        self,
        base_url: str,
        *,
        path: str | None = None,
        token: str = "",
        timeout: int = 60,
        batch_size: int = 500,
    ) -> None:
        if not base_url:
            raise ValueError("统一标签服务的 base_url 不能为空")
        if batch_size < 1:
            raise ValueError("batch_size 必须为正")
        self.base_url = base_url.rstrip("/")
        self.path = path or self.DEFAULT_PATH
        self.token = token
        self.timeout = timeout
        self.batch_size = batch_size

    def write_tags(
        self, requests: Sequence[ImageTagWriteRequest]
    ) -> int:  # pragma: no cover - 需要真实服务
        total = 0
        for chunk in _chunks(requests, self.batch_size):
            total += self._post(chunk)
        return total

    def _post(self, chunk: Sequence[ImageTagWriteRequest]) -> int:  # pragma: no cover
        first = chunk[0]
        idem = f"{first.infer_job_id}:{first.image_id}:{chunk[-1].image_id}:{len(chunk)}"
        headers = {"Content-Type": "application/json", "Idempotency-Key": idem}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = json.dumps(
            {"items": [r.to_payload() for r in chunk]}, ensure_ascii=False, default=str
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{self.path}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise BackendError(f"统一标签服务返回 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise BackendError(f"调用统一标签服务失败: {exc.reason}") from exc
        return int(payload.get("written_count", len(chunk)))


class CaptionVectorSink(ABC):
    """caption 冗余进向量表的出口。

    [S3-03] 二原文：caption「以 tag_category=CAPTION 的特殊标签写入图片标签表，
    与结构化标签同条记录口径并存，**同时冗余一份到向量表**——结构化过滤和语义检索
    用同一份说明，不用两套维护」。

    「不用两套维护」是这件事的全部理由，所以它必须与标签写入在**同一个批次**里完成：
    分两个作业写就是两套维护，迟早对不上。
    """

    @abstractmethod
    def write_captions(self, rows: Sequence[dict[str, Any]]) -> int:
        """冗余写入，返回写入行数。"""


@dataclass(slots=True)
class InMemoryCaptionVectorSink(CaptionVectorSink):
    """进程内向量表 caption 冗余，单测用。"""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def write_captions(self, rows: Sequence[dict[str, Any]]) -> int:
        self.rows.extend(rows)
        return len(rows)


@dataclass(slots=True)
class SqlCaptionVectorSink(CaptionVectorSink):
    """把 caption 冗余更新进 dwd_mining_image_vector_detail。

    只更新 ``caption_text`` 一列，**不**新建行：向量行由 Embedding 流水线按
    (image_id, embedding_version) 产出（[S3-01] 五：Embedding 走凌晨窗口），
    推理引擎跑在它前面，此时向量行可能还不存在——所以这里是 UPDATE 不是 INSERT，
    更新不到就等下一轮，绝不抢着建一行没有向量的空壳。
    """

    backend: SqlBackend
    batch_size: int = 500

    def write_captions(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        target = qualified(DWD_MINING_IMAGE_VECTOR_DETAIL)
        written = 0
        for chunk in _chunks(rows, self.batch_size):
            # 同一段 caption 往往覆盖多张图？不会——caption 是逐图生成的，
            # 所以这里按 caption 分组没意义，逐行 UPDATE，按 image_id 定位。
            for row in chunk:
                sql = (
                    f"UPDATE {target}\n"
                    f"SET `caption_text` = {literal(row['caption_text'])}\n"
                    f"WHERE `image_id` = {literal(row['image_id'])}"
                )
                self.backend.execute(sql)
                written += 1
        return written


# --------------------------------------------------------------------------- 执行追溯


class InferTaskStatus(str, Enum):
    """推理作业状态。取值域对齐 registry 的 dwd_mining_task_detail.task_status。"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class VlmRunRecord:
    """一次推理作业的追溯记录——落 dwd_mining_task_detail 的那一行。

    与规则挖掘的 ``executor.RuleRunRecord`` 同一张表、不同的列投影
    （:data:`~adas_lakehouse.mining.tables.VLM_TASK_WRITE_COLUMNS`）：
    推理的产出单位是图片，所以多写 ``hit_image_count``；而 4 小时 SLA
    （[S3-04] 三）承诺的是 T+1 批扫描不是 GPU 推理，所以 ``sla_breached`` 不写。
    """

    task_id: str
    run_id: str
    job_id: str
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_seconds: float = 0.0
    scan_low_watermark: datetime | None = None
    scan_high_watermark: datetime | None = None
    candidate_count: int = 0
    inferred_image_count: int = 0
    clip_count: int = 0
    tag_written_count: int = 0
    caption_written_count: int = 0
    rejected_count: int = 0
    resumed_batch_count: int = 0
    failed_batch_count: int = 0
    retry_count: int = 0
    task_status: InferTaskStatus = InferTaskStatus.RUNNING
    error_message: str = ""
    rule_id: str = ""
    rule_version: int = 0
    rule_category: str = ""
    rule_priority: int | None = None
    project_code: str = ""

    def finish(
        self, status: InferTaskStatus, *, at: datetime | None = None, error: str = ""
    ) -> None:
        self.finished_at = at or datetime.now()
        self.elapsed_seconds = max(0.0, (self.finished_at - self.started_at).total_seconds())
        self.task_status = status
        self.error_message = error[:2000]

    def to_row(self) -> dict[str, Any]:
        """渲染成 dwd_mining_task_detail 的一行，列名与顺序取自 registry 派生的投影。"""
        row = {
            "mining_task_id": self.task_id,
            "run_id": self.run_id,
            "rule_id": self.rule_id,
            "rule_version": str(self.rule_version) if self.rule_version else "",
            "rule_category": self.rule_category,
            "rule_priority": self.rule_priority,
            "task_type": VLM_TASK_TYPE,
            "exec_mode": INFER_EXEC_MODE,
            "engine": VLM_INFER_ENGINE,
            "project_code": self.project_code,
            "scan_start_time": self.scan_low_watermark,
            "scan_end_time": self.scan_high_watermark,
            "scan_row_count": self.candidate_count,
            "hit_data_count": self.clip_count,
            "hit_image_count": self.inferred_image_count,
            "tag_write_count": self.tag_written_count,
            "task_status": self.task_status.value,
            "duration_sec": round(self.elapsed_seconds, 3),
            "start_time": self.started_at,
            "end_time": self.finished_at,
            "error_message": self.error_message,
        }
        return {c: row[c] for c in VLM_TASK_WRITE_COLUMNS}

    def describe(self) -> str:
        return (
            f"{self.job_id} [{VLM_TASK_TYPE}/{VLM_INFER_ENGINE}] {self.task_status.value} "
            f"耗时 {self.elapsed_seconds:.1f}s 候选 {self.candidate_count} "
            f"推理 {self.inferred_image_count} 图 / {self.clip_count} clip "
            f"打标 {self.tag_written_count}（caption {self.caption_written_count}）"
            + (f" 判废 {self.rejected_count}" if self.rejected_count else "")
            + (f" 续跑跳过 {self.resumed_batch_count} 批" if self.resumed_batch_count else "")
            + (f" ⚠️失败 {self.failed_batch_count} 批" if self.failed_batch_count else "")
        )


@dataclass(slots=True)
class VlmInferReport:
    """一次推理作业的完整回报。"""

    record: VlmRunRecord
    batches_total: int = 0
    batches_run: int = 0
    batches_skipped: list[int] = field(default_factory=list)
    batches_failed: list[tuple[int, str]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.batches_failed

    def summary(self) -> str:
        return (
            f"{self.record.describe()}；批次 {self.batches_run}/{self.batches_total} 实跑"
            + (f"，跳过 {self.batches_skipped}" if self.batches_skipped else "")
            + (f"，失败 {[i for i, _ in self.batches_failed]}" if self.batches_failed else "")
        )


# --------------------------------------------------------------------------- 引擎


@dataclass(slots=True)
class VlmInferenceEngine:
    """VLM 推理挖掘引擎：漏斗第二层。

    Args:
        client: 多模态大模型客户端。
        tag_sink: 统一标签服务的图片级入口——双输出都从这里出去。
        task_sink: 执行追溯的写入口（复用 ``backends.ResultSink`` 的形状）。
        caption_sink: caption 往向量表冗余的出口（[S3-03] 二）。留空则不冗余，
            并在每次运行时告警——不冗余不是错误，但「结构化过滤与语义检索共用
            同一份说明」这个收益就拿不到了，得让人看见。
        checkpoints: 断点存储。
        batch_size / max_attempts: 见各自常量的说明（均为本项目设计）。
    """

    client: VlmClient
    tag_sink: ModelTagSink
    task_sink: Any = None  # backends.ResultSink，避免为类型标注制造 import 环
    caption_sink: CaptionVectorSink | None = None
    checkpoints: CheckpointStore = field(default_factory=InMemoryCheckpointStore)
    batch_size: int = DEFAULT_INFER_BATCH_SIZE
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    prompt: str = ""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 至少为 1（1 = 不重试）")
        if not self.prompt:
            self.prompt = build_prompt()
        missing = [f for f in ("model_name", "model_version") if not getattr(self.client, f, "")]
        if missing:
            raise ValueError(
                f"VLM 客户端缺少 {missing}——[S3-03] 二③ 要求每条模型标签携带血缘字段"
                f"{list(VLM_REQUIRED_LINEAGE_FIELDS)}，模型名与版本缺了就答不出"
                "「这个标签从哪来」"
            )

    # ---- 选帧：读抽帧引擎的产物 ----

    def candidate_sql(
        self,
        *,
        watermark: Watermark | None = None,
        project_code: str = "",
        data_ids: Sequence[str] = (),
        limit: int = DEFAULT_CANDIDATE_LIMIT,
        alias: str = "frm",
    ) -> str:
        """渲染取候选帧的 SELECT。

        三个条件缺一不可：

        * ``is_keyframe = TRUE`` —— 只取抽帧闸门三选中的关键帧（[S3-02] 二：
          「每 clip 打分选 1~5 关键帧」）。不加这个条件就是把全量帧送进 GPU，
          原文那道「控制推理成本」的闸就白设了；
        * ``desensitize_status = 'passed'`` —— 合规红线在消费侧的第二道闸；
        * 增量水位 —— [S3-04] 三：「基于 _ingest_time / update_time 水位做增量，
          避免每次全表回扫」。推理天天跑，全表回扫的代价是 GPU 秒，比批扫描贵得多。

        排序按 ``keyframe_score`` 降序，兑现原文的「信息密度最高的帧」（[S3-04] 四）。

        Raises:
            ValueError: limit 非正。
        """
        if limit < 1:
            raise ValueError(f"limit 必须为正，收到 {limit}")
        a = ident(alias)
        preds = [
            f"{a}.`is_keyframe` = TRUE",
            f"{a}.`desensitize_status` = {literal(DESENSITIZE_PASSED)}",
        ]
        if watermark is not None:
            preds.append(incremental_predicate(watermark, alias=alias))
        if project_code:
            preds.append(f"{a}.`project_code` = {literal(project_code)}")
        if data_ids:
            ids = ", ".join(literal(d) for d in data_ids)
            preds.append(f"{a}.`data_id` IN ({ids})")
        cols = ",\n  ".join(f"{a}.{ident(c)} AS {ident(c)}" for c in KEYFRAME_READ_COLUMNS)
        header = (
            "-- VLM 推理挖掘 · 选帧（漏斗第二层的入口）\n"
            f"-- [S3-02] 二「推理抽帧」：进入 VLM 推理范围的 clip，每 clip 打分选 "
            f"{INFERENCE_MIN_KEYFRAMES}~{INFERENCE_MAX_KEYFRAMES} 关键帧\n"
            "-- [S3-04] 四：对候选里信息密度最高的帧做语义确认"
        )
        return (
            f"{header}\nSELECT\n  {cols}\n"
            f"FROM {qualified(DWD_MINING_IMAGE_FRAME_DETAIL)} AS {a}\n"
            f"WHERE {join_predicates(preds, 'AND')}\n"
            f"ORDER BY {a}.`keyframe_score` DESC\n"
            f"LIMIT {int(limit)}"
        )

    def load_candidates(
        self,
        backend: SqlBackend,
        *,
        watermark: Watermark | None = None,
        project_code: str = "",
        data_ids: Sequence[str] = (),
        limit: int = DEFAULT_CANDIDATE_LIMIT,
        rule_id: str = "",
        rule_version: int = 0,
    ) -> tuple[list[InferCandidate], list[tuple[str, str]]]:
        """跑选帧 SQL 并还原成候选对象。坏行不拖垮整批。

        Returns:
            ``(候选列表, [(image_id, 错误原因)])``。
        """
        sql = self.candidate_sql(
            watermark=watermark, project_code=project_code, data_ids=data_ids, limit=limit
        )
        rows = backend.query(sql)
        ok: list[InferCandidate] = []
        bad: list[tuple[str, str]] = []
        for row in rows:
            try:
                ok.append(InferCandidate.from_row(row, rule_id=rule_id, rule_version=rule_version))
            except VlmOutputError as exc:
                bad.append((str(row.get("image_id", "<unknown>")), str(exc)))
        return ok, bad

    # ---- GPU 任务描述（不自己调度） ----

    def describe_gpu_task(self, job_id: str, *, batches: int, images: int) -> dict[str, Any]:
        """产出一份可交给 GPU 池的任务描述。

        本引擎**不**决定什么时候能跑：分时错峰、优先级队列、峰值期抢占都归 GPU 池
        （[S3-01] 五）。这里只回报「我要跑多少批、多少图、用什么引擎」，
        由控制面（``mining.plane_adapter``）翻成 TaskEnvelope 交给池子。

        刻意只回标量、不回图片本体或对象存储 key 列表：控制面的信封里
        「不装数据本体」是硬约束（controlplane.contracts.assert_no_master_data）。
        """
        return {
            "job_id": job_id,
            "task_type": VLM_TASK_TYPE,
            "engine": VLM_INFER_ENGINE,
            "serving_runtime": VLM_INFER_SERVING_RUNTIME,
            "batch_count": batches,
            "image_count": images,
            "batch_size": self.batch_size,
            "max_attempts": self.max_attempts,
            "model_name": self.client.model_name,
            "model_version": self.client.model_version,
        }

    # ---- 主流程 ----

    def run(
        self,
        candidates: Sequence[InferCandidate],
        *,
        job_id: str = "",
        run_id: str = "",
        now: datetime | None = None,
        watermark: Watermark | None = None,
        resume: bool = True,
        rule_id: str = "",
        rule_version: int = 0,
        rule_category: str = "",
        rule_priority: int | None = None,
        project_code: str = "",
    ) -> VlmInferReport:
        """跑一次推理作业：编排批次 → 逐批推理（带重试与断点）→ 双输出落库 → 回写追溯。

        断点续跑的语义（对应原文选 Ray 的理由）：

        * ``resume=True``（默认）时，已在 :attr:`checkpoints` 里标记完成的批次**不重跑**；
        * 断点只在「标签与 caption 都写完」之后才打——反过来（先打断点再写库）会在
          写库失败时把那批图永久漏掉，而且没人会发现；
        * 因此本引擎是 **at-least-once**：崩在写库与打点之间的那一批会重写一次，
          靠标签事实表的联合主键 Upsert 消化（[S3-03] 二②：「重复写入无副作用」）。

        Args:
            candidates: 候选帧。
            job_id: 推理作业 ID，同时是断点 key 与标签血缘的 ``infer_job_id``；
                留空则由 run_id 派生。**续跑必须传同一个 job_id**，否则断点对不上。
            run_id: 三级 ID，留空自动生成。
            watermark: 本轮候选的扫描水位，写进执行追溯的「扫描范围」。
            resume: 是否启用断点续跑。False = 无视断点全量重跑（重刷时用）。
            rule_id / rule_version / rule_category / rule_priority:
                把这批候选圈出来的规则——漏斗第一层的血缘，第二层不能断。

        Returns:
            :class:`VlmInferReport`。作业整体不抛异常：单批失败计入
            ``batches_failed``，输出不合格计入 ``rejected``。
        """
        started = now or datetime.now()
        rid = run_id or str(new_run_id("mining", started))
        jid = job_id or f"vlm_{rid}"
        record = VlmRunRecord(
            task_id=f"{rid}_{jid}",
            run_id=rid,
            job_id=jid,
            started_at=started,
            candidate_count=len(candidates),
            rule_id=rule_id,
            rule_version=rule_version,
            rule_category=rule_category,
            rule_priority=rule_priority,
            project_code=project_code or _first_project(candidates),
        )
        if watermark is not None:
            record.scan_low_watermark, record.scan_high_watermark = watermark.low, watermark.high

        # 耗时用单调钟量，不用 now 与 datetime.now() 作差——调用方为了可重放常常传一个
        # 固定的 now（单测、回刷），那样算出来的 duration_sec 是「现在离那个时刻多久」，
        # 动辄几万秒，直接把执行追溯表的耗时列变成垃圾。
        wall_start = _monotonic()
        batches = plan_batches(candidates, job_id=jid, batch_size=self.batch_size)
        report = VlmInferReport(record=record, batches_total=len(batches))
        done = self.checkpoints.completed(jid) if resume else set()
        if done:
            logger.info("作业 %s 断点续跑：已完成 %d 批，本轮跳过", jid, len(done))

        clips: set[str] = set()
        for batch in batches:
            if batch.batch_index in done:
                report.batches_skipped.append(batch.batch_index)
                record.resumed_batch_count += 1
                continue
            try:
                outputs = self._infer_with_retry(batch, record)
            except VlmInferError as exc:
                # 单批打光重试次数：记账，继续跑下一批。整个作业不因一批坏图作废——
                # 这正是「断点续跑」要的行为，下次续跑会重试这一批。
                report.batches_failed.append((batch.batch_index, str(exc)))
                record.failed_batch_count += 1
                logger.error(
                    "作业 %s 批次 %d 重试 %d 次仍失败: %s",
                    jid,
                    batch.batch_index,
                    self.max_attempts,
                    exc,
                )
                continue

            report.batches_run += 1
            tags, captions, rejected = self._to_write_requests(batch, outputs, jid, rid, started)
            report.rejected.extend(rejected)
            record.rejected_count += len(rejected)

            written = self.tag_sink.write_tags(tags) if tags else 0
            record.tag_written_count += written
            record.caption_written_count += sum(1 for t in tags if t.is_caption)
            tagged_images = {t.image_id for t in tags}
            record.inferred_image_count += len(tagged_images)
            clips.update(c.data_id for c in batch.candidates if c.image_id in tagged_images)

            if captions:
                self._redundant_caption_write(captions, jid)

            # 断点在**落库之后**才打
            self.checkpoints.mark_done(jid, batch.batch_index)

        record.clip_count = len(clips)
        # 有任何一批没跑成就记 failed，哪怕别的批都成功了。
        # 把「部分失败」记成 success 会让执行追溯表看起来一切正常，
        # 而那几批图其实一条标签都没有——原文（[S3-04] 一）要求追溯是为了让规则效果
        # 「可度量，而不是配完就黑盒」，掩盖缺口正好是它的反面。
        # 实际落了多少由 hit_image_count / tag_write_count 如实回答，
        # 哪几批要重试由 error_message 指名。
        if report.batches_failed:
            status = InferTaskStatus.FAILED
            error = (
                f"{len(report.batches_failed)}/{report.batches_total} 个批次失败，"
                f"下次以同一 job_id 续跑会重试：{[i for i, _ in report.batches_failed]}"
            )
        else:
            status = InferTaskStatus.SUCCESS
            error = ""
        elapsed = max(0.0, _monotonic() - wall_start)
        record.finish(status, at=started + timedelta(seconds=elapsed), error=error)
        self._record_task(record)
        logger.info("VLM 推理结束：%s", report.summary())
        return report

    # ---- 内部 ----

    def _infer_with_retry(self, batch: InferBatch, record: VlmRunRecord) -> list[VlmOutput]:
        """带重试地跑一个批次。

        只重试 :class:`VlmInferError`（调用侧失败，重试有意义）。
        :class:`VlmOutputError` 不在这里处理——输出不合格重试还是不合格，
        它在 :meth:`_to_write_requests` 里被判废并计入 rejected。

        Raises:
            VlmInferError: 打光 max_attempts 仍失败。
        """
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                note = getattr(self.client, "note_batch", None)
                if callable(note):
                    note(batch.batch_index)
                return self.client.infer_batch(batch.candidates, prompt=self.prompt)
            except VlmInferError as exc:
                last = exc
                if attempt < self.max_attempts:
                    record.retry_count += 1
                    logger.warning(
                        "作业 %s 批次 %d 第 %d/%d 次推理失败，重试: %s",
                        batch.job_id,
                        batch.batch_index,
                        attempt,
                        self.max_attempts,
                        exc,
                    )
        raise VlmInferError(
            f"批次 {batch.batch_index}（{batch.size} 张图）重试 {self.max_attempts} 次仍失败: {last}"
        )

    def _to_write_requests(
        self,
        batch: InferBatch,
        outputs: Sequence[VlmOutput],
        job_id: str,
        run_id: str,
        moment: datetime,
    ) -> tuple[list[ImageTagWriteRequest], list[dict[str, Any]], list[tuple[str, str]]]:
        """把双输出翻成标签服务的入参 + 向量表的 caption 冗余行。

        **双输出在这里各走各的路，但同批发出**：结构化标签写成普通类别的标签行，
        caption 写成 ``tag_category=CAPTION`` 的特殊标签行（[S3-03] 二），
        两者落同一张 dwd_mining_image_tag_detail，「与结构化标签同条记录口径并存」。

        Returns:
            ``(标签请求, caption 冗余行, [(image_id, 判废原因)])``。
        """
        by_id = {c.image_id: c for c in batch.candidates}
        tags: list[ImageTagWriteRequest] = []
        captions: list[dict[str, Any]] = []
        rejected: list[tuple[str, str]] = []

        for out in outputs:
            cand = by_id.get(out.image_id)
            if cand is None:
                # 模型回了一个不在本批里的 image_id：一律丢弃。放行的后果是把标签
                # 挂到别的图上，而这种错在标签表里看不出来。
                rejected.append((out.image_id, "推理产出的 image_id 不在本批候选内"))
                continue
            common = {
                "image_id": cand.image_id,
                "data_id": cand.data_id,
                "infer_job_id": job_id,
                "run_id": run_id,
                "model_name": self.client.model_name,
                "model_version": self.client.model_version,
                "rule_id": cand.rule_id,
                "rule_version": cand.rule_version,
                "parent_artifact_id": cand.parent_artifact_id,
                "camera_id": cand.camera_id,
                "project_code": cand.project_code,
                "vehicle_code": cand.vehicle_code,
                "tagged_at": moment,
            }
            for tag in out.tags:
                tags.append(
                    ImageTagWriteRequest(
                        raw_tag=tag.raw_tag,
                        tag_category="",  # 类别由字典映射裁定，模型不自封
                        confidence=tag.confidence,
                        **common,
                    )
                )
            # caption：特殊类别 CAPTION，正文进 caption_text（[S3-03] 二）
            tags.append(
                ImageTagWriteRequest(
                    raw_tag=VLM_CAPTION_TAG_CATEGORY,
                    tag_category=VLM_CAPTION_TAG_CATEGORY,
                    caption_text=out.caption,
                    confidence=out.top_confidence,
                    **common,
                )
            )
            captions.append(
                {
                    "image_id": cand.image_id,
                    "data_id": cand.data_id,
                    "caption_text": out.caption,
                    "model_name": self.client.model_name,
                    "model_version": self.client.model_version,
                    "artifact_status": ArtifactStatus.ACTIVE.value,
                    "run_id": run_id,
                }
            )
        return tags, captions, rejected

    def _redundant_caption_write(self, captions: Sequence[dict[str, Any]], job_id: str) -> None:
        """caption 往向量表冗余一份。失败只告警，不回滚已写入的标签。

        [S3-03] 二只说「同时冗余一份到向量表」，没说冗余失败怎么办。
        本项目的判断：标签表是事实源，向量表那份是**副本**；副本写失败不该让事实
        回滚，下一轮 Embedding 流水线还能补。反过来做（为副本回滚事实）才是错的。
        """
        if self.caption_sink is None:
            logger.warning(
                "作业 %s 产出 %d 条 caption，但未配置 caption_sink——"
                "向量表不会拿到这份冗余，「结构化过滤与语义检索共用同一份说明」"
                "（[S3-03] 二）这个收益拿不到",
                job_id,
                len(captions),
            )
            return
        try:
            self.caption_sink.write_captions(captions)
        except (BackendError, OSError) as exc:  # noqa: BLE001 - 副本失败不回滚事实
            logger.error(
                "作业 %s 的 caption 向量表冗余写入失败（标签已写入，不回滚）: %s", job_id, exc
            )

    def _record_task(self, record: VlmRunRecord) -> None:
        """回写执行追溯。写失败只记日志——追溯写不进去是运维问题，不该判作业失败。"""
        if self.task_sink is None:
            return
        try:
            self.task_sink.write_results([record.to_row()])
        except Exception as exc:  # noqa: BLE001
            logger.error("执行追溯回写 %s 失败: %s", DWD_MINING_TASK_DETAIL.resolve(), exc)


# --------------------------------------------------------------------------- 工具


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _first_project(candidates: Sequence[InferCandidate]) -> str:
    for c in candidates:
        if c.project_code:
            return c.project_code
    return ""


def keyframe_watermark_column() -> str:
    """选帧增量扫描读哪一列水位。抽帧表在 DWD 层，因此是 ``update_time``。

    暴露成函数而不是常量，是为了让「层级 → 水位列」这条规则只有
    :func:`~adas_lakehouse.mining.watermark.watermark_column` 一处实现。
    """
    return watermark_column(Layer.DWD)


# --------------------------------------------------------------------------- import 期自检

_PAYLOAD_KEYS = set(
    ImageTagWriteRequest(
        image_id="i",
        data_id="d",
        raw_tag="t",
        tag_category="",
        infer_job_id="j",
        run_id="r",
        model_name="m",
        model_version="v",
    )
    .to_payload()
    .keys()
)
_REGISTRY_KEYS = set(IMAGE_TAG_WRITE_COLUMNS)
assert _PAYLOAD_KEYS == _REGISTRY_KEYS, (
    "ImageTagWriteRequest.to_payload() 的键必须逐字等于 registry 的列名："
    f"多了 {sorted(_PAYLOAD_KEYS - _REGISTRY_KEYS)}，少了 {sorted(_REGISTRY_KEYS - _PAYLOAD_KEYS)}；"
    "表结构的唯一事实源是 catalog/tables/_mining.py"
)
assert DWD_MINING_IMAGE_TAG_DETAIL.spec() is not None, "图片标签表必须登记在 registry 里"
del _PAYLOAD_KEYS, _REGISTRY_KEYS
