"""Ray + vLLM 推理引擎：真的能派发任务的那一层，不是一张选型表。

原文第五章技术选型逐字：

  「**GPU 推理调度选 Ray + vLLM**（Triton 适合单模型服务化，但**缺任务编排与断点续跑**）」

这句话是本模块存在的全部理由，而且它定义了验收标准：我们放弃 Triton 换来的两样东西
——**任务编排**与**断点续跑**——必须真的在这里实现，否则这个选型就是白选的。

  ==========  ========================================================
  任务编排    :meth:`RayInferenceEngine.plan` 把一次推理任务切成有序批次
              （:class:`InferenceBatch`），批次是调度、计费、重试的最小单位；
              批次之间可被 GPU 池打断（见下），这正是 Triton 的单模型服务化
              做不到的事。
  断点续跑    每个批次跑完立刻落 :class:`CheckpointStore`。任务被抢占 / 缩容
              驱逐 / 进程崩溃之后重新派发时，:meth:`RayInferenceEngine.dispatch`
              只跑**没跑过的批次**，已完成的批次不重算，GPU 秒不白烧。
  ==========  ========================================================

与 GPU 池的关系（原文第三章「推理引擎按优先级抢 GPU」）：本引擎自己**不**决定
什么时候能跑，它把信封交给 :class:`~.gpu.GpuPool`，拿到租约才开工，跑完/被抢就交卡。
所以「分时错峰 + 优先级 + 抢占」只有一套实现，不会出现引擎和池子各调度各的。

依赖：``ray`` / ``vllm`` **本模块一行都不 import**，由部署侧注入
（见 :class:`RayVllmBatchRunner`）——它们是 GPU 节点的运行时，不是湖仓建模包的依赖，
本包的「裸装即可用」纪律也不允许引入未声明的库。默认的 :class:`EchoBatchRunner`
让编排、断点、抢占整条链路在什么都没装的环境里可运行、可单测。

⚠️ 原文未明确，本项目设计：批大小、vLLM 引擎参数（tensor_parallel_size 等）、
Ray 集群规格。原文只给了「Ray + vLLM」这个选型和它的理由。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..controlplane import constants as K
from ..controlplane.contracts import TaskEnvelope, TaskKind
from .engines import engine_for
from .gpu import GpuLease, GpuPool, GpuWindow, current_window, fits_window

__all__ = [
    "TRITON_REJECTION_REASON",
    "RAY_CAPABILITIES_REQUIRED",
    "DEFAULT_INFERENCE_BATCH_SIZE",
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
    "RayInferenceEngine",
    "describe_engine_choice",
]

_log = logging.getLogger(__name__)

#: 原文第五章逐字：为什么不选 Triton。
TRITON_REJECTION_REASON: str = "Triton 适合单模型服务化，但缺任务编排与断点续跑"

#: 上面那句话的反面：选了 Ray + vLLM 就必须交付这两样能力，本模块逐条实现。
RAY_CAPABILITIES_REQUIRED: tuple[str, ...] = ("任务编排", "断点续跑")

#: ⚠️ 原文未明确，本项目设计：一个批次多少个 clip。取 64 是常见的 vLLM 连续批处理
#: 规模量级；批越大吞吐越高，但被抢占时作废的工作量也越大（断点粒度就是批粒度）。
DEFAULT_INFERENCE_BATCH_SIZE: int = 64


# --------------------------------------------------------------------------- 配置


@dataclass(frozen=True, slots=True)
class VllmEngineConfig:
    """vLLM 引擎参数。

    ⚠️ 原文未明确，本项目设计：原文只写了「Ray + vLLM」四个字，没给任何引擎参数。
    这里给的是一组能跑起来的默认值，真实取值要按显存与模型规模调。

    :param model: 模型标识（走平台支撑层的模型注册表，原文第三章）
    :param tensor_parallel_size: 张量并行度，通常等于单副本占用的卡数
    :param max_num_seqs: vLLM 连续批处理的最大并发序列数
    :param gpu_memory_utilization: 显存占用上限比例
    :param batch_size: 任务编排的批大小，同时也是断点粒度
    """

    model: str = "qwen-vl-chat"
    tensor_parallel_size: int = 1
    max_num_seqs: int = 256
    gpu_memory_utilization: float = 0.90
    dtype: str = "bfloat16"
    batch_size: int = DEFAULT_INFERENCE_BATCH_SIZE

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size 必须 ≥ 1")
        if self.tensor_parallel_size < 1:
            raise ValueError("tensor_parallel_size 必须 ≥ 1")
        if not 0.0 < self.gpu_memory_utilization <= 1.0:
            raise ValueError("gpu_memory_utilization 必须落在 (0, 1]")

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "tensor_parallel_size": self.tensor_parallel_size,
            "max_num_seqs": self.max_num_seqs,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "dtype": self.dtype,
            "batch_size": self.batch_size,
        }


@dataclass(frozen=True, slots=True)
class RayClusterSpec:
    """Ray 集群规格。原文第五章部署表格：GPU 资源池 =「Ray 集群（VLM 推理 vLLM / Embedding）」。

    ⚠️ 原文未明确，本项目设计：address / namespace / 每 actor 占卡数。
    """

    address: str = "auto"
    namespace: str = "adas-mining"
    num_gpus_per_actor: float = 1.0
    #: 与 GPU 池共享：Ray 的 actor 数不应超过池子的槽位数，否则 Ray 内部又排一次队，
    #: 池子的优先级与抢占语义就被绕过去了。
    respects_pool_slots: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "namespace": self.namespace,
            "num_gpus_per_actor": self.num_gpus_per_actor,
            "respects_pool_slots": self.respects_pool_slots,
            "zone": "GPU 资源池",
        }


# --------------------------------------------------------------------------- 编排


@dataclass(frozen=True, slots=True)
class InferenceBatch:
    """一个批次：任务编排的最小单位，也是断点续跑的断点粒度。"""

    run_id: str
    batch_index: int
    data_ids: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.data_ids)


@dataclass(frozen=True, slots=True)
class BatchResult:
    """一个批次的产出。``rows`` 交给 :class:`~.execution.DataPlane` 写湖仓。"""

    batch_index: int
    rows: tuple[Mapping[str, Any], ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class BatchRunner(Protocol):
    """真正在 GPU 上跑一个批次的东西。注入式，因此本模块零外部依赖。"""

    def run_batch(self, batch: InferenceBatch, config: VllmEngineConfig) -> BatchResult: ...


class EchoBatchRunner:
    """默认 runner：不碰 GPU，按输入原样产出行。让整条链路可单测。

    它产出的行形状与真实 VLM 推理一致（每个 clip 一行语义标签 + caption 占位），
    因此上层的写湖仓、血缘、回报逻辑都能被完整验证。
    """

    def __init__(self) -> None:
        self.calls: list[int] = []

    def run_batch(self, batch: InferenceBatch, config: VllmEngineConfig) -> BatchResult:
        self.calls.append(batch.batch_index)
        rows = tuple(
            {
                "data_id": data_id,
                "model": config.model,
                "batch_index": batch.batch_index,
            }
            for data_id in batch.data_ids
        )
        return BatchResult(batch.batch_index, rows, {"batch_size": batch.size})


class RayVllmBatchRunner:
    """真实 runner：在 Ray remote actor 里跑 vLLM。

    ``ray`` 与 ``vllm`` 由**调用方注入**，本模块不 import 它们，一行都不。原因有二：

      1. 本包的依赖纪律是「裸装即可用」——``src/`` 下任何模块都不得依赖未在
         ``pyproject`` optional-dependencies 里声明过的库，而 ray / vllm 是 GPU 节点
         上的运行时，不是湖仓建模包的依赖；
      2. Ray 的 API 跨版本差异大，把版本适配留在部署侧比钉死在库里更耐用。

    接入方式（在 GPU 资源区的部署代码里）::

        import ray
        from vllm import LLM

        @ray.remote(num_gpus=1)
        def infer(data_ids, engine_args):
            llm = LLM(model=engine_args["model"],
                      tensor_parallel_size=engine_args["tensor_parallel_size"],
                      gpu_memory_utilization=engine_args["gpu_memory_utilization"])
            return [{"data_id": d, "caption": llm.generate(...)} for d in data_ids]

        runner = RayVllmBatchRunner(ray_module=ray, remote_fn=infer)
        engine = RayInferenceEngine(pool=pool, runner=runner)

    :param ray_module: 已 import 的 ``ray`` 模块
    :param remote_fn: ``@ray.remote`` 装饰过的批量推理函数
    :param cluster: 集群规格，用于首次 ``ray.init``
    """

    def __init__(
        self,
        *,
        ray_module: Any = None,
        remote_fn: Any = None,
        cluster: RayClusterSpec | None = None,
    ) -> None:
        self.cluster = cluster or RayClusterSpec()
        self._ray = ray_module
        self._remote_fn = remote_fn
        self._lock = threading.Lock()

    def _ensure_ray(self) -> Any:
        if self._ray is None:
            raise RuntimeError(
                "RayVllmBatchRunner 未注入 ray_module：ray / vllm 是 GPU 节点的运行时，"
                "本包不声明也不 import 它们。请在部署代码里 `import ray` 后传进来"
                "（用法见类文档），或改用 EchoBatchRunner"
                "（编排、断点、抢占全链路照样可跑，只是不产生真实推理结果）"
            )
        with self._lock:
            if not self._ray.is_initialized():
                self._ray.init(address=self.cluster.address, namespace=self.cluster.namespace)
        return self._ray

    def run_batch(self, batch: InferenceBatch, config: VllmEngineConfig) -> BatchResult:
        ray = self._ensure_ray()
        if self._remote_fn is None:
            raise RuntimeError(
                "未注入 remote_fn：请传入一个 @ray.remote(num_gpus=...) 装饰的函数，"
                "它内部用 vllm.LLM(model=config.model, tensor_parallel_size=...) 做批量推理"
            )
        ref = self._remote_fn.remote(list(batch.data_ids), config.as_dict())
        rows = ray.get(ref)
        return BatchResult(batch.batch_index, tuple(rows), {"batch_size": batch.size})


# --------------------------------------------------------------------------- 断点


@runtime_checkable
class CheckpointStore(Protocol):
    """断点存储：记住某次 run 已经跑完了哪些批次。

    注意它存的是**数据面自己的运行态**，不是控制面状态：控制面只知道任务在 RUNNING，
    断点是数据面为了不重算而做的本地优化，丢了最多是多跑一遍，不影响正确性
    （artifact_id 由内容哈希决定，重跑幂等）。
    """

    def completed(self, run_id: str) -> frozenset[int]: ...

    def mark(self, run_id: str, batch_index: int, *, rows: int) -> None: ...

    def clear(self, run_id: str) -> None: ...


class InMemoryCheckpointStore:
    """默认断点存储：进程内。真实部署应换成 OSS / Redis 上的键值。"""

    def __init__(self) -> None:
        self._done: dict[str, dict[int, int]] = {}
        self._lock = threading.RLock()

    def completed(self, run_id: str) -> frozenset[int]:
        with self._lock:
            return frozenset(self._done.get(run_id, {}))

    def mark(self, run_id: str, batch_index: int, *, rows: int) -> None:
        with self._lock:
            self._done.setdefault(run_id, {})[batch_index] = rows

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._done.pop(run_id, None)

    def rows_done(self, run_id: str) -> int:
        with self._lock:
            return sum(self._done.get(run_id, {}).values())


# --------------------------------------------------------------------------- 派发


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """一次派发的结果。

    三种结局，调用方必须分开处理：

      · ``admitted=False``：没拿到卡（窗口不对或队列排不上）。任务已经在池子里排着，
        下一轮 :meth:`RayInferenceEngine.dispatch` 会再试——**不是失败**；
      · ``preempted=True``：跑到一半被抢占。已完成批次的 checkpoint 保留，
        任务已被池子按原序重新入队，下次接着跑；
      · ``finished=True``：全部批次跑完，卡已交还。
    """

    task_id: str
    run_id: str
    admitted: bool
    batches_total: int
    batches_completed: int
    batches_ran_this_round: int
    rows: tuple[Mapping[str, Any], ...] = ()
    preempted: bool = False
    lease: GpuLease | None = None
    window: GpuWindow = GpuWindow.DAY_INFERENCE
    reason: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.admitted and not self.preempted and self.batches_completed >= self.batches_total

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "admitted": self.admitted,
            "batches_total": self.batches_total,
            "batches_completed": self.batches_completed,
            "batches_ran_this_round": self.batches_ran_this_round,
            "row_count": len(self.rows),
            "preempted": self.preempted,
            "finished": self.finished,
            "window": self.window.value,
            "reason": self.reason,
            "metrics": dict(self.metrics),
        }


class RayInferenceEngine:
    """Ray + vLLM 推理引擎：把信封变成批次、抢到卡才跑、跑一批存一个断点。

    典型用法（一次派发没跑完就再派发一次，直到 ``finished``）::

        pool = GpuPool(slots=2)
        engine = RayInferenceEngine(pool=pool)
        result = engine.dispatch(envelope, data_ids, now=datetime(2026, 3, 2, 10, 0))
        while not result.finished:
            result = engine.dispatch(envelope, data_ids, now=...)

    :param pool: 共享 GPU 池。VLM 推理与 Embedding 用**同一个**（原文第五章）
    :param config: vLLM 引擎参数
    :param runner: 批次执行器，默认 :class:`EchoBatchRunner`
    :param checkpoints: 断点存储，默认 :class:`InMemoryCheckpointStore`
    :param cluster: Ray 集群规格
    """

    def __init__(
        self,
        *,
        pool: GpuPool | None = None,
        config: VllmEngineConfig | None = None,
        runner: BatchRunner | None = None,
        checkpoints: CheckpointStore | None = None,
        cluster: RayClusterSpec | None = None,
    ) -> None:
        self.pool = pool or GpuPool()
        self.config = config or VllmEngineConfig()
        self.runner: BatchRunner = runner or EchoBatchRunner()
        self.checkpoints: CheckpointStore = checkpoints or InMemoryCheckpointStore()
        self.cluster = cluster or RayClusterSpec()

    # ---- 任务编排 ----

    def plan(self, envelope: TaskEnvelope, data_ids: Sequence[str]) -> tuple[InferenceBatch, ...]:
        """把一次推理任务切成有序批次——「任务编排」的实现。

        :raises ValueError: 任务种类不吃 GPU，或 data_ids 为空
        """
        self._assert_gpu_kind(envelope)
        if not data_ids:
            raise ValueError(f"任务 {envelope.task_id} 没有任何 data_id，无从编排批次")
        size = self.config.batch_size
        return tuple(
            InferenceBatch(envelope.run_id, index, tuple(data_ids[start : start + size]))
            for index, start in enumerate(range(0, len(data_ids), size))
        )

    def resume_point(self, run_id: str) -> frozenset[int]:
        """已完成的批次号——「断点续跑」的断点。"""
        return self.checkpoints.completed(run_id)

    # ---- 派发 ----

    def dispatch(
        self,
        envelope: TaskEnvelope,
        data_ids: Sequence[str],
        *,
        now: datetime | None = None,
    ) -> DispatchResult:
        """派发一次：入池 → 抢卡 → 从断点接着跑 → 交卡。

        没抢到卡不算失败（``admitted=False``），任务留在池子里等下一轮；
        跑到一半被抢占也不算失败（``preempted=True``），断点留着下次续。
        """
        moment = now or datetime.now()
        self._assert_gpu_kind(envelope)
        batches = self.plan(envelope, data_ids)
        window = current_window(moment)

        self.pool.offer(envelope)
        self.pool.schedule(moment)
        lease = self.pool.lease_for(envelope.task_id)
        done = set(self.checkpoints.completed(envelope.run_id))

        if lease is None:
            return DispatchResult(
                task_id=envelope.task_id,
                run_id=envelope.run_id,
                admitted=False,
                batches_total=len(batches),
                batches_completed=len(done),
                batches_ran_this_round=0,
                window=window,
                reason=self._wait_reason(envelope, window),
                metrics={"queued": True, "queue_depth": len(self.pool.queued_task_ids())},
            )

        resumed_from = len(done)
        rows: list[Mapping[str, Any]] = []
        ran = 0
        preempted = False
        for batch in batches:
            if batch.batch_index in done:
                continue  # 断点续跑：跑过的批次不重算
            if self.pool.lease_for(envelope.task_id) is None:
                # 跑批期间卡被抢走了（峰值期高优任务抢占，或缩容驱逐）。
                # 池子已经把本任务按原序推回队列，这里只要停手、把断点留住。
                preempted = True
                break
            result = self.runner.run_batch(batch, self.config)
            rows.extend(result.rows)
            self.checkpoints.mark(envelope.run_id, batch.batch_index, rows=len(result.rows))
            done.add(batch.batch_index)
            ran += 1

        if not preempted:
            self.pool.release(envelope.task_id)

        return DispatchResult(
            task_id=envelope.task_id,
            run_id=envelope.run_id,
            admitted=True,
            batches_total=len(batches),
            batches_completed=len(done),
            batches_ran_this_round=ran,
            rows=tuple(rows),
            preempted=preempted,
            lease=lease,
            window=window,
            reason="被抢占，断点已保留，等下一轮续跑" if preempted else "",
            metrics={
                "engine": engine_for(envelope.kind).key,
                "runtime": engine_for(envelope.kind).runtime,
                "model": self.config.model,
                "resumed_from_batch": resumed_from,
                "batch_size": self.config.batch_size,
            },
        )

    def run_to_completion(
        self,
        envelope: TaskEnvelope,
        data_ids: Sequence[str],
        *,
        now: datetime | None = None,
        max_rounds: int = 100,
    ) -> DispatchResult:
        """反复派发直到跑完。被抢占就重排队再来，断点保证不重算。

        :param max_rounds: 轮数上限，防止窗口一直不对时死循环
        :raises RuntimeError: 超过轮数上限仍未跑完（通常意味着窗口判定或优先级配错了）
        """
        result = self.dispatch(envelope, data_ids, now=now)
        rounds = 1
        rows = list(result.rows)
        while not result.finished:
            if rounds >= max_rounds:
                raise RuntimeError(
                    f"任务 {envelope.task_id} 派发 {rounds} 轮仍未跑完："
                    f"{result.reason or '窗口或优先级可能配错了'}"
                )
            result = self.dispatch(envelope, data_ids, now=now)
            rows.extend(result.rows)
            rounds += 1
        return DispatchResult(
            task_id=result.task_id,
            run_id=result.run_id,
            admitted=True,
            batches_total=result.batches_total,
            batches_completed=result.batches_completed,
            batches_ran_this_round=result.batches_ran_this_round,
            rows=tuple(rows),
            preempted=False,
            lease=result.lease,
            window=result.window,
            metrics={**dict(result.metrics), "rounds": rounds},
        )

    # ---- 内部 ----

    @staticmethod
    def _assert_gpu_kind(envelope: TaskEnvelope) -> None:
        if envelope.kind not in (TaskKind.VLM_INFERENCE, TaskKind.EMBEDDING):
            raise ValueError(
                f"Ray + vLLM 引擎只接 VLM 推理与 Embedding（两者共享同一个 GPU 池），"
                f"收到 {envelope.kind.value}"
            )

    def _wait_reason(self, envelope: TaskEnvelope, window: GpuWindow) -> str:
        if not fits_window(envelope.kind, window):
            if envelope.kind is TaskKind.EMBEDDING:
                return (
                    f"Embedding 走凌晨窗口，必须凌晨 {K.EMBEDDING_WINDOW_DEADLINE_HOUR} 点前完成；"
                    f"当前是{window.value}，排队等窗口"
                )
            return f"VLM 推理走白天窗口；当前是{window.value}，排队等窗口"
        return f"窗口正确但无空闲卡，按优先级排队（当前优先级 {envelope.priority}）"


def describe_engine_choice() -> dict[str, Any]:
    """把「为什么是 Ray + vLLM 而不是 Triton」摊开，并指出对应实现在哪。"""
    return {
        "chosen": "Ray + vLLM",
        "rejected": "Triton",
        "reason": TRITON_REJECTION_REASON,
        "capabilities_we_must_deliver": {
            "任务编排": "RayInferenceEngine.plan() 切批次；GpuPool 负责窗口/优先级/抢占",
            "断点续跑": "CheckpointStore 按批次落断点；dispatch() 只跑未完成批次",
        },
    }
