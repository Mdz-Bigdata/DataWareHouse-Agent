"""GPU 资源池：分时复用 + 优先级队列 + 抢占 + 弹性扩缩 + 向量化成本等级。

原文第五章，这一段的每一个字都要落地：

  「GPU 是最贵的资源，利用率设计是重头戏：**VLM 推理与 Embedding 共享同一个 GPU 池**
    ——推理任务白天按优先级队列执行，Embedding 走凌晨窗口（**凌晨 6 点前完成**），
    用时间错峰避免资源争抢；**峰值期低优任务可被抢占**。此外向量化本身也分成本等级：
    高价值数据（规则命中 / 事件抽帧 / VLM 标签）优先向量化，普通数据抽样处理——
    **GPU 成本花在刀刃上**。」

以及原文第五章部署表格里 GPU 资源池那一行的伸缩策略逐字：
  「Ray 集群（VLM 推理 vLLM / Embedding）｜**分时复用 + 优先级队列 + 弹性扩缩**」

拆成五条可执行规则：
  1. 共享同池：VLM 推理与 Embedding 用同一个 :class:`GpuPool`；
  2. 时间错峰：Embedding 只在凌晨窗口跑，且必须在凌晨
     :data:`~adas_lakehouse.controlplane.constants.EMBEDDING_WINDOW_DEADLINE_HOUR`
     （= 6）点前完成；白天窗口留给推理；
  3. 优先级队列 + 抢占：峰值期低优任务可被高优任务抢占（完整语义见
     :func:`can_preempt` 与 :class:`PreemptionRecord` 的文档）；
  4. 弹性扩缩：:meth:`GpuPool.scale_to` / :meth:`GpuPool.desired_slots`；
  5. 成本分级：高价值数据全量向量化，普通数据抽样。

⚠️ 原文未明确，本项目设计（下面每一处都单独标注）：窗口的起点小时、抽样比例、
「峰值期」的判定、抢占的优先级差、以及单卡并发数。原文只给了「凌晨 6 点前完成」
这一个硬数字。
"""

from __future__ import annotations

import heapq
import itertools
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..controlplane import constants as K
from ..controlplane.contracts import TaskEnvelope, TaskKind

__all__ = [
    "GpuWindow",
    "ValueTier",
    "EvictReason",
    "EMBEDDING_WINDOW_DEADLINE_HOUR",
    "NIGHT_WINDOW_START_HOUR",
    "DAY_WINDOW_START_HOUR",
    "PEAK_HOURS",
    "ORDINARY_DATA_SAMPLE_RATIO",
    "DEFAULT_SLOTS_PER_GPU",
    "DEFAULT_PREEMPT_PRIORITY_GAP",
    "HIGH_VALUE_SOURCES",
    "GPU_POOL_SCALING_POLICY",
    "GpuLease",
    "GpuPool",
    "PreemptionRecord",
    "ScaleOutcome",
    "current_window",
    "fits_window",
    "is_peak",
    "embedding_deadline_ok",
    "can_preempt",
    "classify_value_tier",
    "vectorization_quota",
    "batch_quota",
    "describe_gpu_policy",
]

# --------------------------------------------------------------------------- 时间窗口

#: 原文第五章逐字：Embedding「凌晨 6 点前完成」。
EMBEDDING_WINDOW_DEADLINE_HOUR: int = K.EMBEDDING_WINDOW_DEADLINE_HOUR  # 6

#: ⚠️ 原文未明确，本项目设计：「凌晨窗口」的起点取 0 点（自然日切换即开跑），
#: 这样凌晨窗口是 [0, 6)，正好在原文给的 6 点截止前结束。
NIGHT_WINDOW_START_HOUR: int = 0

#: ⚠️ 原文未明确，本项目设计：「白天」的起点取 6 点——凌晨窗口一结束就把池子交还推理。
DAY_WINDOW_START_HOUR: int = EMBEDDING_WINDOW_DEADLINE_HOUR  # 6

#: ⚠️ 原文未明确，本项目设计：「峰值期」判定为 9-12 点与 14-18 点两段工作高峰。
#: 原文只说「峰值期低优任务可被抢占」，没定义峰值期。
PEAK_HOURS: frozenset[int] = frozenset({9, 10, 11, 14, 15, 16, 17})

#: ⚠️ 原文未明确，本项目设计：普通数据向量化抽样比例 10%。
#: 原文只说「普通数据抽样处理」，没给比例。真实比例应按 GPU 预算回归调参。
ORDINARY_DATA_SAMPLE_RATIO: float = 0.1

#: ⚠️ 原文未明确，本项目设计：单张 GPU 卡同时承载的作业数。
DEFAULT_SLOTS_PER_GPU: int = 1

#: ⚠️ 原文未明确，本项目设计：触发抢占所需的优先级差。原文只说「低优任务可被抢占」，
#: 没说「低多少算低」。取 2 是为了留出一档缓冲：优先级只差 1 的两个任务谁抢谁都不划算
#: （被抢者损失的 GPU 时间大于抢占者提前完成的收益），差到 2 档才认定为「低优」。
DEFAULT_PREEMPT_PRIORITY_GAP: int = 2

#: 原文第五章部署表格 GPU 资源池那一行的伸缩策略，逐字。
GPU_POOL_SCALING_POLICY: str = "分时复用 + 优先级队列 + 弹性扩缩"


class GpuWindow(str, Enum):
    """GPU 池的分时窗口。用时间错峰避免资源争抢（原文第五章）。"""

    NIGHT_EMBEDDING = "night_embedding"  # 凌晨窗口，Embedding 专用
    DAY_INFERENCE = "day_inference"  # 白天窗口，VLM 推理按优先级队列执行

    @property
    def preferred_kind(self) -> TaskKind:
        return TaskKind.EMBEDDING if self is GpuWindow.NIGHT_EMBEDDING else TaskKind.VLM_INFERENCE


def current_window(now: datetime | None = None) -> GpuWindow:
    """当前处于哪个窗口。

    凌晨窗口 [0, 6)；其余时间为白天窗口。
    """
    hour = (now or datetime.now()).hour
    if NIGHT_WINDOW_START_HOUR <= hour < EMBEDDING_WINDOW_DEADLINE_HOUR:
        return GpuWindow.NIGHT_EMBEDDING
    return GpuWindow.DAY_INFERENCE


def fits_window(kind: TaskKind, window: GpuWindow) -> bool:
    """分时错峰的判定：Embedding 只在凌晨窗口跑；VLM 推理白天跑。

    原文第五章：「推理任务白天按优先级队列执行，Embedding 走凌晨窗口
    （凌晨 6 点前完成），用时间错峰避免资源争抢」——两类任务各有各的窗口，
    窗口不对就排队等，不硬抢。这是「分时复用」四个字的判定函数。
    """
    if kind is TaskKind.EMBEDDING:
        return window is GpuWindow.NIGHT_EMBEDDING
    return window is GpuWindow.DAY_INFERENCE


def is_peak(now: datetime | None = None) -> bool:
    """是否处于峰值期（⚠️ 峰值期定义为本项目设计，见 :data:`PEAK_HOURS`）。

    峰值期是抢占的**唯一**授权窗口：原文只说「峰值期低优任务可被抢占」，
    没有授权任何其它时段抢占，因此本实现在非峰值期一律走排队（理由见 :func:`can_preempt`）。
    """
    return (now or datetime.now()).hour in PEAK_HOURS


def embedding_deadline_ok(started_at: datetime, finished_at: datetime) -> bool:
    """校验一次 Embedding 是否踩住了「凌晨 6 点前完成」这条硬线（原文第五章）。"""
    if finished_at < started_at:
        raise ValueError("finished_at 早于 started_at")
    return finished_at.hour < EMBEDDING_WINDOW_DEADLINE_HOUR or (
        finished_at.hour == EMBEDDING_WINDOW_DEADLINE_HOUR
        and finished_at.minute == 0
        and finished_at.second == 0
    )


# --------------------------------------------------------------------------- 成本等级


class ValueTier(str, Enum):
    """向量化成本等级（原文第五章：「向量化本身也分成本等级」）。"""

    #: 高价值数据：规则命中 / 事件抽帧 / VLM 标签 —— 优先向量化（全量）
    HIGH = "high"
    #: 普通数据 —— 抽样处理
    ORDINARY = "ordinary"


#: 高价值数据的三个来源，逐字取自原文第五章括号内容。
HIGH_VALUE_SOURCES: tuple[str, ...] = ("规则命中", "事件抽帧", "VLM 标签")

#: 来源标记 -> 等级。⚠️ 原文未明确，本项目设计：原文给的是中文描述，
#: 这里定义了对应的机器可读来源标记。
_SOURCE_TIER: dict[str, ValueTier] = {
    "rule_hit": ValueTier.HIGH,  # 规则命中
    "event_trigger": ValueTier.HIGH,  # 事件抽帧
    "vlm_tag": ValueTier.HIGH,  # VLM 标签
    "uniform_sampling": ValueTier.ORDINARY,
    "unknown": ValueTier.ORDINARY,
}


def classify_value_tier(source: str) -> ValueTier:
    """按数据来源判成本等级。未知来源保守归为普通数据（省 GPU）。"""
    return _SOURCE_TIER.get(source, ValueTier.ORDINARY)


def vectorization_quota(source: str, candidate_count: int) -> int:
    """算一次向量化实际要处理多少条——「GPU 成本花在刀刃上」的算术形式。

    高价值数据全量；普通数据按 :data:`ORDINARY_DATA_SAMPLE_RATIO`
    （⚠️ 10% 为本项目设计）抽样，至少留 1 条以便链路可观测。

    :param source: 数据来源标记
    :param candidate_count: 候选条数
    :raises ValueError: 候选条数为负
    """
    if candidate_count < 0:
        raise ValueError(f"candidate_count 不能为负：{candidate_count}")
    if candidate_count == 0:
        return 0
    if classify_value_tier(source) is ValueTier.HIGH:
        return candidate_count
    return max(1, int(candidate_count * ORDINARY_DATA_SAMPLE_RATIO))


# --------------------------------------------------------------------------- 租约


@dataclass(frozen=True, slots=True)
class GpuLease:
    """一次 GPU 占用（租约）。

    租约是**数据面的运行态**，不进控制面：控制面只知道任务是 RUNNING，
    不知道它占的是哪张卡、什么时候被抢占过。租约随进程重建——池子重启后
    队列重放即可，因为主数据在湖仓、断点在 checkpoint（见 ray_engine 模块）。

    :param preemptible: 本租约是否可被抢占。判定规则见 :meth:`GpuPool._occupy`
    :param window: 拿到卡时所处的分时窗口，用于审计「有没有人在错误的窗口占卡」
    """

    task_id: str
    kind: TaskKind
    priority: int
    slot: int
    acquired_at: datetime
    preemptible: bool
    window: GpuWindow = GpuWindow.DAY_INFERENCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "priority": self.priority,
            "slot": self.slot,
            "acquired_at": self.acquired_at.isoformat(timespec="seconds"),
            "preemptible": self.preemptible,
            "window": self.window.value,
        }


class EvictReason(str, Enum):
    """租约被提前收回的原因。"""

    #: 峰值期高优任务抢占（原文第五章唯一授权的抢占场景）
    PEAK_PREEMPTION = "peak_preemption"
    #: 弹性缩容，槽位被裁掉（原文第五章「弹性扩缩」）
    SCALE_IN = "scale_in"


@dataclass(frozen=True, slots=True)
class PreemptionRecord:
    """一次抢占/驱逐的留痕——「抢完被抢的任务怎么办」的答案就写在这里。

    被抢的任务会经历三件事，缺一不可：

      1. **立刻交卡**：租约作废，槽位当场让给抢占者；
      2. **原位重排队**：原信封按**原入队序号**推回优先级队列（``requeued=True``），
         因此它排在同优先级的后来者**前面**，不会被反复插队饿死；
      3. **留痕**：本记录进 :meth:`GpuPool.preemption_log`，运维能回答
         「这张卡这一小时被抢了几次、谁抢的、赔进去多少 GPU 秒」。

    执行侧还要做第四件事（见 :mod:`.ray_engine`）：被抢任务已完成的批次
    **保留 checkpoint**，下次拿到卡从断点继续，不从头重跑——这正是原文放弃 Triton
    选 Ray + vLLM 的理由（「Triton 适合单模型服务化，但缺任务编排与断点续跑」）。

    :param wasted_seconds: 被抢任务这一轮已经烧掉的 GPU 秒。它是抢占的代价，
        也是「非峰值期不抢占」这条规则的依据——见 :func:`can_preempt`。
    """

    victim_task_id: str
    victim_kind: TaskKind
    victim_priority: int
    winner_task_id: str
    winner_priority: int
    slot: int
    at: datetime
    reason: EvictReason
    requeued: bool
    wasted_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "victim_task_id": self.victim_task_id,
            "victim_kind": self.victim_kind.value,
            "victim_priority": self.victim_priority,
            "winner_task_id": self.winner_task_id,
            "winner_priority": self.winner_priority,
            "slot": self.slot,
            "at": self.at.isoformat(timespec="seconds"),
            "reason": self.reason.value,
            "requeued": self.requeued,
            "wasted_seconds": round(self.wasted_seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class ScaleOutcome:
    """一次弹性扩缩的结果（原文第五章 GPU 资源池「弹性扩缩」）。"""

    before: int
    after: int
    evicted_task_ids: tuple[str, ...] = ()

    @property
    def direction(self) -> str:
        if self.after > self.before:
            return "scale_out"
        if self.after < self.before:
            return "scale_in"
        return "noop"

    def as_dict(self) -> dict[str, Any]:
        return {
            "before": self.before,
            "after": self.after,
            "direction": self.direction,
            "evicted_task_ids": list(self.evicted_task_ids),
        }


def can_preempt(
    winner_priority: int,
    victim: GpuLease,
    *,
    now: datetime | None = None,
    gap: int = DEFAULT_PREEMPT_PRIORITY_GAP,
) -> bool:
    """**谁能抢谁**：抢占的完整判定，四个条件全中才准抢。

    1. **必须是峰值期**（:func:`is_peak`）。原文只授权了这一个场景：「峰值期低优任务
       可被抢占」。**非峰值期为什么不抢占**：抢占不是零成本——被抢任务这一轮烧掉的
       GPU 秒全部作废（:attr:`PreemptionRecord.wasted_seconds`），重跑还要重新加载
       模型权重。非峰值期队列不紧张、空槽很快就会出现，等一会儿的代价远小于重跑的
       代价，净收益为负，所以一律排队不抢。峰值期正相反：空槽遥遥无期，高优任务干等
       的机会成本超过被抢任务的重跑成本，这时抢占才划算。
    2. **被抢者可被抢**（``victim.preemptible``）。判定见 :meth:`GpuPool._occupy`：
       低于默认优先级的推理任务、以及有整个凌晨窗口可以重来的 Embedding 才可被抢；
       高优（``priority <= PRIORITY_DEFAULT``）的推理任务不可被抢，否则「优先级」失去意义。
    3. **优先级确实更高，且高出一档以上**（``victim.priority >= winner + gap``）。
       数值越小越优先；差距不到 :data:`DEFAULT_PREEMPT_PRIORITY_GAP` 不认定为「低优」。
    4. 抢占者自己必须是当前窗口该跑的任务——这一条由 :meth:`GpuPool.schedule` 在调用
       本函数之前用 :meth:`GpuPool._fits_window` 保证，所以本函数不重复检查。

    :param winner_priority: 抢占者的优先级（数值越小越优先）
    :param victim: 被抢的租约
    """
    if not is_peak(now):
        return False
    if not victim.preemptible:
        return False
    return victim.priority >= winner_priority + gap


# --------------------------------------------------------------------------- 池


@dataclass(order=True, slots=True)
class _QueueItem:
    priority: int
    seq: int
    envelope: TaskEnvelope = field(compare=False)


class GpuPool:
    """VLM 推理与 Embedding **共享的同一个** GPU 池（原文第五章）。

    四条策略，对应原文第五章 GPU 资源池那一行的「分时复用 + 优先级队列 + 弹性扩缩」
    加上正文里的「峰值期低优任务可被抢占」：

      · **分时复用**：窗口不匹配的任务排队等窗口，不硬抢（:meth:`_fits_window`）；
      · **优先级队列**：``envelope.priority`` 数值越小越优先（同控制面同一套口径），
        同优先级按入队先后（FIFO），被抢占后按**原入队序号**回队，不会被后来者插队饿死；
      · **抢占**：峰值期高优任务可抢低优任务的卡，完整语义见 :func:`can_preempt`；
      · **弹性扩缩**：:meth:`scale_to` 改槽位数，:meth:`desired_slots` 给扩缩建议。

    线程安全：所有公开方法都在同一把 :class:`threading.RLock` 下。

    :param slots: 总槽位数（卡数 × 每卡并发），⚠️ 每卡并发默认 1 为本项目设计
    :param preempt_priority_gap: 触发抢占的优先级差，见 :data:`DEFAULT_PREEMPT_PRIORITY_GAP`
    :param max_slots: 弹性扩容上限。⚠️ 原文未明确，本项目设计：默认初始槽位的 4 倍
    """

    def __init__(
        self,
        slots: int = 8,
        *,
        preempt_priority_gap: int = DEFAULT_PREEMPT_PRIORITY_GAP,
        max_slots: int | None = None,
    ) -> None:
        if slots < 1:
            raise ValueError("GPU 槽位数必须 ≥ 1")
        if preempt_priority_gap < 1:
            raise ValueError("抢占优先级差必须 ≥ 1，否则同优先级任务会互相踢")
        self._slots = slots
        self._max_slots = max_slots if max_slots is not None else slots * 4
        if self._max_slots < slots:
            raise ValueError("max_slots 不能小于初始槽位数")
        self._gap = preempt_priority_gap
        self._lock = threading.RLock()
        self._counter = itertools.count()
        self._queue: list[_QueueItem] = []
        self._running: dict[int, GpuLease] = {}
        #: task_id -> 信封。被抢占后要靠它把任务原样推回队列
        self._envelopes: dict[str, TaskEnvelope] = {}
        #: task_id -> 首次入队序号。回队时复用，保证被抢者不被后来者插队
        self._seq: dict[str, int] = {}
        #: 被抢占、正等待重跑的任务
        self._preempted: set[str] = set()
        self._preemptions: list[PreemptionRecord] = []

    # ---- 入队 ----

    def offer(self, envelope: TaskEnvelope) -> bool:
        """把 GPU 任务放进优先级队列。非 GPU 任务直接拒收。

        幂等：已在队列里或已占着卡的任务重复 offer 返回 ``False`` 且不做任何事——
        否则同一个 task_id 会被派到两个槽位上，同时写同一批产物。

        :return: 本次是否真的新入队
        :raises ValueError: 任务种类不吃 GPU
        """
        if envelope.kind not in (TaskKind.VLM_INFERENCE, TaskKind.EMBEDDING):
            raise ValueError(
                f"任务 {envelope.task_id} 的种类 {envelope.kind.value} 不吃 GPU；"
                f"GPU 池只接 VLM 推理与 Embedding（原文：两者共享同一个 GPU 池）"
            )
        with self._lock:
            if self._is_running(envelope.task_id) or self._is_queued(envelope.task_id):
                return False
            seq = self._seq.get(envelope.task_id)
            if seq is None:
                seq = next(self._counter)
                self._seq[envelope.task_id] = seq
            self._envelopes[envelope.task_id] = envelope
            self._preempted.discard(envelope.task_id)
            heapq.heappush(self._queue, _QueueItem(envelope.priority, seq, envelope))
            return True

    # ---- 调度 ----

    def schedule(self, now: datetime | None = None) -> list[GpuLease]:
        """跑一轮调度，返回本轮新拿到卡的任务。

        顺序：先按窗口筛出「该跑的」，再按优先级出队占空槽；空槽用完后，
        若 :func:`can_preempt` 放行（峰值期 + 低优 + 差一档以上），则抢占低优任务——
        被抢者当场交卡、按原序回队、进抢占日志（见 :class:`PreemptionRecord`）。

        队头任务既拿不到空槽也抢不到卡时**停止本轮**而不是跳过它去看下一个：
        跳过会让低优任务在高优任务前面拿到卡，优先级队列就名存实亡了。
        """
        moment = now or datetime.now()
        window = current_window(moment)
        granted: list[GpuLease] = []

        with self._lock:
            deferred: list[_QueueItem] = []
            while self._queue:
                item = heapq.heappop(self._queue)
                env = item.envelope
                if not self._fits_window(env.kind, window):
                    deferred.append(item)  # 错峰：等自己的窗口
                    continue
                slot = self._free_slot()
                if slot is not None:
                    granted.append(self._occupy(slot, env, moment, window))
                    continue
                victim = self._preempt_candidate(env, moment)
                if victim is not None:
                    self._evict(
                        victim,
                        at=moment,
                        reason=EvictReason.PEAK_PREEMPTION,
                        winner_task_id=env.task_id,
                        winner_priority=env.priority,
                    )
                    granted.append(self._occupy(victim.slot, env, moment, window))
                    continue
                deferred.append(item)  # 没卡也抢不到，排下一轮
                break
            for item in deferred:
                heapq.heappush(self._queue, item)
        return granted

    def release(self, task_id: str) -> bool:
        """作业跑完释放卡。释放后该 task_id 的排队序号也一并清掉。"""
        with self._lock:
            for slot, lease in list(self._running.items()):
                if lease.task_id == task_id:
                    del self._running[slot]
                    self._forget(task_id)
                    return True
        return False

    def cancel(self, task_id: str) -> bool:
        """把任务从池子里彻底摘掉：在跑的交卡，在排队的出队。

        控制面取消任务时要调它，否则被取消的任务还占着槽位或队列位置。

        :return: 是否真的摘掉了东西
        """
        with self._lock:
            removed = self.release(task_id)
            before = len(self._queue)
            self._queue = [item for item in self._queue if item.envelope.task_id != task_id]
            heapq.heapify(self._queue)
            removed = removed or len(self._queue) != before
            self._forget(task_id)
            return removed

    # ---- 弹性扩缩（原文第五章：分时复用 + 优先级队列 + 弹性扩缩）----

    def scale_to(self, slots: int, *, now: datetime | None = None) -> ScaleOutcome:
        """改变池子的槽位数。

        扩容（``slots`` 变大）立刻生效，下一轮 :meth:`schedule` 就能用上新槽位。
        缩容要把编号 ≥ 新槽位数的租约收回：这些任务与被抢占的任务走**完全一样**的
        善后路径——交卡、按原序回队、进抢占日志（reason = ``scale_in``），因此缩容
        不会丢任务。

        :raises ValueError: 槽位数越界 [1, max_slots]
        """
        moment = now or datetime.now()
        with self._lock:
            if slots < 1:
                raise ValueError("GPU 槽位数必须 ≥ 1")
            if slots > self._max_slots:
                raise ValueError(f"扩容上限为 {self._max_slots} 槽，请求 {slots}")
            before = self._slots
            self._slots = slots
            evicted: list[str] = []
            for slot, lease in sorted(self._running.items()):
                if slot >= slots:
                    self._evict(
                        lease,
                        at=moment,
                        reason=EvictReason.SCALE_IN,
                        winner_task_id="",
                        winner_priority=lease.priority,
                    )
                    evicted.append(lease.task_id)
            return ScaleOutcome(before, slots, tuple(evicted))

    def desired_slots(self, now: datetime | None = None) -> int:
        """给出「现在应该有多少槽位」的扩缩建议。

        ⚠️ 原文未明确，本项目设计：原文只说「弹性扩缩」，没给扩缩函数。这里取
        「正在跑的 + 当前窗口该跑却在排队的」，并夹在 ``[1, max_slots]`` 之间——
        意思是把当前窗口能立刻消化的积压一次性拉满，窗口外的任务不参与扩容决策
        （它们本来就该等窗口，为它们扩容等于在错峰之外又制造争抢）。
        """
        moment = now or datetime.now()
        window = current_window(moment)
        with self._lock:
            runnable = sum(
                1 for item in self._queue if self._fits_window(item.envelope.kind, window)
            )
            return max(1, min(self._max_slots, len(self._running) + runnable))

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def slots(self) -> int:
        return self._slots

    # ---- 观测 ----

    def utilization(self) -> float:
        """槽位利用率。GPU 是最贵的资源，这个数就是「利用率设计」的成绩单。"""
        with self._lock:
            return len(self._running) / self._slots

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        """池子全景，供运维面板。传 ``now`` 可看指定时刻的窗口判定。"""
        moment = now or datetime.now()
        with self._lock:
            return {
                "slots": self._slots,
                "max_slots": self._max_slots,
                "running": [lease.as_dict() for lease in self._running.values()],
                "queued": len(self._queue),
                "queued_task_ids": [item.envelope.task_id for item in sorted(self._queue)],
                "preempted_task_ids": sorted(self._preempted),
                "utilization": len(self._running) / self._slots,
                "window": current_window(moment).value,
                "is_peak": is_peak(moment),
                "embedding_deadline_hour": EMBEDDING_WINDOW_DEADLINE_HOUR,
                "preemption_count": len(self._preemptions),
                "scaling_policy": GPU_POOL_SCALING_POLICY,
            }

    def queued_task_ids(self) -> list[str]:
        with self._lock:
            return [item.envelope.task_id for item in sorted(self._queue)]

    def running_task_ids(self) -> list[str]:
        with self._lock:
            return [lease.task_id for lease in self._running.values()]

    def lease_for(self, task_id: str) -> GpuLease | None:
        """查某个任务当前的租约；没占卡返回 ``None``。"""
        with self._lock:
            for lease in self._running.values():
                if lease.task_id == task_id:
                    return lease
        return None

    def is_preempted(self, task_id: str) -> bool:
        """该任务是否被抢占过且仍在等重跑。

        执行侧靠它决定「这次是从头跑还是从 checkpoint 续跑」。
        """
        with self._lock:
            return task_id in self._preempted

    def preemption_log(self) -> list[PreemptionRecord]:
        """抢占/驱逐留痕，按发生顺序。"""
        with self._lock:
            return list(self._preemptions)

    # ---- 内部 ----

    _fits_window = staticmethod(fits_window)

    def _free_slot(self) -> int | None:
        for slot in range(self._slots):
            if slot not in self._running:
                return slot
        return None

    def _is_running(self, task_id: str) -> bool:
        return any(lease.task_id == task_id for lease in self._running.values())

    def _is_queued(self, task_id: str) -> bool:
        return any(item.envelope.task_id == task_id for item in self._queue)

    def _forget(self, task_id: str) -> None:
        self._envelopes.pop(task_id, None)
        self._seq.pop(task_id, None)
        self._preempted.discard(task_id)

    def _occupy(
        self, slot: int, envelope: TaskEnvelope, moment: datetime, window: GpuWindow
    ) -> GpuLease:
        # 谁可被抢：
        #   · Embedding 天然可被抢占——它有整个凌晨窗口可以重来；
        #   · 推理任务只有**低于默认优先级**（数值 > PRIORITY_DEFAULT）的才可被抢，
        #     高优推理任务不可被抢，否则「优先级」这三个字就没有意义了。
        preemptible = envelope.kind is TaskKind.EMBEDDING or envelope.priority > K.PRIORITY_DEFAULT
        lease = GpuLease(
            task_id=envelope.task_id,
            kind=envelope.kind,
            priority=envelope.priority,
            slot=slot,
            acquired_at=moment,
            preemptible=preemptible,
            window=window,
        )
        self._running[slot] = lease
        self._envelopes.setdefault(envelope.task_id, envelope)
        self._preempted.discard(envelope.task_id)
        return lease

    def _preempt_candidate(self, envelope: TaskEnvelope, moment: datetime) -> GpuLease | None:
        """挑一个可被抢的租约。判定逐条见 :func:`can_preempt`。"""
        victims = [
            lease
            for lease in self._running.values()
            if can_preempt(envelope.priority, lease, now=moment, gap=self._gap)
        ]
        if not victims:
            return None
        # 先抢优先级最低的（数值最大）；同优先级抢**最晚开工**的那个——它烧掉的
        # GPU 秒最少，赔得最少（见 PreemptionRecord.wasted_seconds）。
        return max(victims, key=lambda lease: (lease.priority, lease.acquired_at))

    def _evict(
        self,
        lease: GpuLease,
        *,
        at: datetime,
        reason: EvictReason,
        winner_task_id: str,
        winner_priority: int,
    ) -> PreemptionRecord:
        """收回一个租约并把被收回的任务原样送回队列。

        「抢完被抢的任务怎么办」的实现：交卡 → 原序回队 → 留痕 → 打上
        ``preempted`` 标记让执行侧知道下次要从 checkpoint 续跑。
        """
        self._running.pop(lease.slot, None)
        envelope = self._envelopes.get(lease.task_id)
        requeued = False
        if envelope is not None:
            seq = self._seq.get(lease.task_id)
            if seq is None:
                seq = next(self._counter)
                self._seq[lease.task_id] = seq
            heapq.heappush(self._queue, _QueueItem(envelope.priority, seq, envelope))
            requeued = True
        self._preempted.add(lease.task_id)
        record = PreemptionRecord(
            victim_task_id=lease.task_id,
            victim_kind=lease.kind,
            victim_priority=lease.priority,
            winner_task_id=winner_task_id,
            winner_priority=winner_priority,
            slot=lease.slot,
            at=at,
            reason=reason,
            requeued=requeued,
            wasted_seconds=max(0.0, (at - lease.acquired_at).total_seconds()),
        )
        self._preemptions.append(record)
        return record


def describe_gpu_policy() -> dict[str, Any]:
    """把 GPU 策略摊成一个字典，供运维面板与文档引用。"""
    return {
        "shared_pool": "VLM 推理与 Embedding 共享同一个 GPU 池",
        "day_window": f"[{DAY_WINDOW_START_HOUR}:00, 24:00) 推理任务按优先级队列执行",
        "night_window": (
            f"[{NIGHT_WINDOW_START_HOUR}:00, {EMBEDDING_WINDOW_DEADLINE_HOUR}:00) "
            f"Embedding 走凌晨窗口，凌晨 {EMBEDDING_WINDOW_DEADLINE_HOUR} 点前完成"
        ),
        "scaling_policy": GPU_POOL_SCALING_POLICY,
        "preemption": "峰值期低优任务可被抢占",
        "preemption_rules": {
            "when": f"仅峰值期（⚠️ 本项目设计：{sorted(PEAK_HOURS)} 点）",
            "who": (
                "高优任务抢低优任务；被抢者必须 preemptible"
                "（Embedding 或优先级数值 > "
                f"{K.PRIORITY_DEFAULT} 的推理任务），且优先级至少低 "
                f"{DEFAULT_PREEMPT_PRIORITY_GAP} 档"
            ),
            "victim_fate": "当场交卡 → 按原入队序号回队 → 进抢占日志 → 下次从 checkpoint 续跑",
            "why_not_off_peak": (
                "非峰值期空槽很快出现，等待成本低于重跑成本（被抢任务已烧的 GPU 秒作废 + "
                "重新加载模型权重），抢占净收益为负；原文也只授权了峰值期"
            ),
        },
        "cost_tiers": {
            "high": f"高价值数据（{' / '.join(HIGH_VALUE_SOURCES)}）优先向量化，全量处理",
            "ordinary": f"普通数据抽样处理，抽样比例 {ORDINARY_DATA_SAMPLE_RATIO}（⚠️ 本项目设计）",
        },
        "goal": "GPU 成本花在刀刃上",
    }


def batch_quota(sources: Iterable[tuple[str, int]]) -> dict[str, int]:
    """批量算向量化配额：``[(来源, 候选数), ...] -> {来源: 实际处理数}``。"""
    return {source: vectorization_quota(source, count) for source, count in sources}
