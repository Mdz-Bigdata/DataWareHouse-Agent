"""数据面执行侧：接信封 → 跑引擎 → 写湖仓 → 回指针。

这是「数据面负责实际计算」的落点。它与控制面的全部交互只有两个对象：
收 :class:`~adas_lakehouse.controlplane.contracts.TaskEnvelope`，
回 :class:`~adas_lakehouse.controlplane.contracts.RunReport`。

三条铁律（全部来自原文第二、四章）：
  1. **产物直接写湖仓**，不经控制面中转——「平台从不自建数据通道，永远走在湖仓既有的
     链路上」；
  2. **回报只给指针**（artifact_id / 表名 / 行数），主数据一条都不回流；
  3. **重刷不覆盖**：同一 clip 因算法更新重刷时 data_id 不变，新产物生成新 artifact_id，
     旧产物保留并标记 ``superseded``（见 ids 模块规则三）。

本模块提供两样东西：
  · :class:`DataPlane` —— 执行器门面，负责 ID 生成、血缘登记、写湖仓、组装回报；
  · :class:`BaseSubsystemAdapter` —— 给 mining / sampling / tags 复用的适配器骨架。
    子系统在自己的 ``plane_adapter.py`` 里继承它、实现 :meth:`~BaseSubsystemAdapter.run`，
    即可满足控制面的 ``SubsystemAdapter`` 接口约定，无需了解控制面内部实现。
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..controlplane import constants as K
from ..controlplane.contracts import (
    ArtifactRef,
    RunReport,
    TaskEnvelope,
    TaskKind,
    TaskState,
    assert_no_control_state,
    assert_no_master_data,
)
from ..ids import ArtifactStatus, derive_artifact_id, parse_run_id
from .engines import EngineSpec, engine_for
from .gpu import GpuLease, GpuPool, current_window, fits_window

__all__ = [
    "LakeSink",
    "DryRunLakeSink",
    "LineageSink",
    "NullLineageSink",
    "Neo4jLineageSink",
    "ExecutionContext",
    "ExecutionResult",
    "GpuAdmission",
    "DataPlane",
    "BaseSubsystemAdapter",
]

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- 出口


@runtime_checkable
class LakeSink(Protocol):
    """把产物写进湖仓表。数据面唯一的写出口。"""

    def write(self, table: str, rows: list[Mapping[str, Any]]) -> int: ...


class DryRunLakeSink:
    """默认实现：只记账不落盘，让本模块零外部依赖即可运行与单测。"""

    def __init__(self) -> None:
        self.written: list[tuple[str, int]] = []
        self._lock = threading.Lock()

    def write(self, table: str, rows: list[Mapping[str, Any]]) -> int:
        with self._lock:
            self.written.append((table, len(rows)))
        _log.info("DryRunLakeSink: %s <- %d 行", table, len(rows))
        return len(rows)


@runtime_checkable
class LineageSink(Protocol):
    """血缘登记出口。原文第二章对齐约定五：「抽帧产物登记血缘，全链路可追溯」。"""

    def record(self, artifact: ArtifactRef, *, run_id: str, stage: str) -> None: ...


class NullLineageSink:
    """默认实现：不登记，只是把边收在内存里供断言。"""

    def __init__(self) -> None:
        self.edges: list[tuple[str, str | None, str]] = []

    def record(self, artifact: ArtifactRef, *, run_id: str, stage: str) -> None:
        self.edges.append((artifact.artifact_id, artifact.parent_artifact_id, run_id))


class Neo4jLineageSink:
    """Neo4j 血缘登记。``neo4j`` 驱动延迟 import；连接信息取 ``config.settings().neo4j``。

    口诀（见 config.Neo4jConfig）：图库找关系、湖仓取明细。
    这里只写 ``DERIVED_FROM`` 边——明细在湖仓，冗余的 ``parent_artifact_id``
    则作为图库对账的兜底。
    """

    def __init__(self, *, driver: Any = None) -> None:
        self._driver = driver

    def _get_driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        try:
            from neo4j import GraphDatabase  # 延迟 import
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Neo4jLineageSink 需要 neo4j 驱动；请 `pip install neo4j`，"
                "或改用 NullLineageSink（血缘也可事后由湖仓 parent_artifact_id 重建）"
            ) from exc
        from ..config import settings

        cfg = settings().neo4j
        self._driver = GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))
        return self._driver

    def record(self, artifact: ArtifactRef, *, run_id: str, stage: str) -> None:
        from ..config import settings

        cypher = (
            "MERGE (a:Artifact {artifact_id: $artifact_id}) "
            "SET a.stage = $stage, a.run_id = $run_id, a.table = $table, a.status = $status "
            "WITH a "
            "FOREACH (_ IN CASE WHEN $parent IS NULL THEN [] ELSE [1] END | "
            "  MERGE (p:Artifact {artifact_id: $parent}) "
            "  MERGE (a)-[:DERIVED_FROM]->(p))"
        )
        params = {
            "artifact_id": artifact.artifact_id,
            "parent": artifact.parent_artifact_id,
            "stage": stage,
            "run_id": run_id,
            "table": artifact.table,
            "status": artifact.status.value,
        }
        with self._get_driver().session(database=settings().neo4j.database) as session:
            session.run(cypher, **params)


# --------------------------------------------------------------------------- 执行


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """一次执行的上下文，交给子系统的 ``run()``。"""

    envelope: TaskEnvelope
    engine: EngineSpec
    started_at: datetime
    #: 算法版本，形如 v1 / v3.2。进 artifact_id，决定重刷时是否产出新产物。
    algo_version: str = "v1"

    @property
    def stage(self) -> str:
        """artifact_id 的 stage 段，由 run_id 解析得到，保证两级 ID 的 stage 一致。"""
        return parse_run_id(self.envelope.run_id).stage


@dataclass(slots=True)
class ExecutionResult:
    """子系统 ``run()`` 的返回：产出了哪些行、挂在哪个父产物下。

    ``rows`` 是要写进湖仓的业务行——它在数据面内部流转，**不会**进 RunReport，
    因此不违反「回报只给指针」。
    """

    target_table: str
    rows: list[Mapping[str, Any]] = field(default_factory=list)
    parent_artifact_id: str | None = None
    #: 产物内容指纹的原料。相同输入 + 相同算法版本 → 相同 artifact_id → 重试幂等
    content_signature: str = ""
    #: 这次产出针对的 clip。一个任务可能覆盖多个 clip，则给多条 ExecutionResult
    data_id: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True, slots=True)
class GpuAdmission:
    """一次 GPU 准入的结果。

    ``granted=False`` **不是失败**：任务已经在池子里按优先级排着，等窗口或等空卡，
    下一次准入尝试会再试。原文第五章的「分时复用 + 优先级队列」在执行侧就长这样。
    """

    granted: bool
    lease: GpuLease | None = None
    reason: str = ""

    def as_metrics(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {"gpu_granted": self.granted}
        if self.lease is not None:
            metrics.update(
                {
                    "gpu_slot": self.lease.slot,
                    "gpu_window": self.lease.window.value,
                    "gpu_preemptible": self.lease.preemptible,
                }
            )
        if self.reason:
            metrics["gpu_wait_reason"] = self.reason
        return metrics


class DataPlane:
    """数据面执行器门面。

    :param lake: 湖仓写出口，默认 :class:`DryRunLakeSink`
    :param lineage: 血缘登记出口，默认 :class:`NullLineageSink`
    :param gpu_pool: GPU 池；VLM 推理与 Embedding 共享同一个池（原文第五章）
    """

    def __init__(
        self,
        *,
        lake: LakeSink | None = None,
        lineage: LineageSink | None = None,
        gpu_pool: GpuPool | None = None,
    ) -> None:
        self.lake: LakeSink = lake or DryRunLakeSink()
        self.lineage: LineageSink = lineage or NullLineageSink()
        self.gpu_pool = gpu_pool or GpuPool()

    # ---- 执行 ----

    def execute(
        self,
        envelope: TaskEnvelope,
        results: list[ExecutionResult],
        *,
        algo_version: str = "v1",
        started_at: datetime | None = None,
    ) -> RunReport:
        """把子系统算出来的结果落湖仓，并组装成只含指针的运行回报。

        步骤：
          1. 为每条结果派生 artifact_id（内容哈希 → 重试天然幂等）；
          2. 把 artifact_id / parent_artifact_id / artifact_status 补进每一行，
             再整批写入目标表（产物直接进湖仓，不经控制面）；
          3. 登记血缘（DERIVED_FROM 边 + 湖仓冗余父 ID 兜底）；
          4. 组装 :class:`RunReport`——只有 ID、表名、行数。

        :raises ValueError: 结果缺少 data_id（产物必须挂在 clip 锚点上）
        """
        begin = started_at or datetime.now()
        engine = engine_for(envelope.kind)
        artifacts: list[ArtifactRef] = []
        total_rows = 0

        for result in results:
            if not result.data_id:
                raise ValueError(
                    f"任务 {envelope.task_id} 的产出缺少 data_id；"
                    f"每个产物都必须挂在 clip 级锚点下，否则血缘断链"
                )
            stage = parse_run_id(envelope.run_id).stage
            payload = result.content_signature or _default_signature(envelope, result)
            artifact_id = str(derive_artifact_id(result.data_id, stage, algo_version, payload))
            ref = ArtifactRef(
                artifact_id=artifact_id,
                table=result.target_table,
                row_count=len(result.rows),
                parent_artifact_id=result.parent_artifact_id,
                status=ArtifactStatus.ACTIVE,
            )
            rows = [
                {
                    **dict(row),
                    "artifact_id": artifact_id,
                    "parent_artifact_id": result.parent_artifact_id,
                    "artifact_status": ArtifactStatus.ACTIVE.value,
                    "run_id": envelope.run_id,
                    "data_id": result.data_id,
                }
                for row in result.rows
            ]
            # 守卫（反向）：产物表里不许夹带控制面运行态——「数据面不存状态」。
            # 回流表（ods_mining_rule_config / dwd_mining_task_detail）例外，见守卫文档。
            assert_no_control_state(
                rows,
                table=result.target_table,
                where=f"DataPlane.execute(task={envelope.task_id})",
            )
            written = self.lake.write(result.target_table, rows) if rows else 0
            total_rows += written
            self.lineage.record(ref, run_id=envelope.run_id, stage=stage)
            artifacts.append(ref)

        metrics: dict[str, Any] = {
            "engine": engine.key,
            "zone": engine.zone.value,
            "artifact_count": len(artifacts),
            "elapsed_seconds": round((datetime.now() - begin).total_seconds(), 3),
        }
        for result in results:
            metrics.update(result.metrics)
        # 守卫：回报里绝不能夹带主数据
        assert_no_master_data(metrics, where=f"DataPlane.execute(task={envelope.task_id}).metrics")

        return RunReport(
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            state=TaskState.SUCCEEDED,
            engine=f"{engine.name_cn}（{engine.runtime}）",
            artifacts=tuple(artifacts),
            rows_written=total_rows,
            started_at=begin,
            finished_at=datetime.now(),
            message="; ".join(r.message for r in results if r.message),
            metrics=metrics,
        )

    def supersede(self, old: ArtifactRef, new_artifact_id: str) -> ArtifactRef:
        """重刷：旧产物标 ``superseded``，新产物另起 artifact_id（ids 规则三）。

        注意这是**状态变更**，不是删除——「重刷不覆盖」，v3/v4 效果要能对比。
        """
        if old.artifact_id == new_artifact_id:
            raise ValueError("新旧 artifact_id 相同，说明输入与算法版本都没变，无需重刷")
        superseded = ArtifactRef(
            artifact_id=old.artifact_id,
            table=old.table,
            row_count=old.row_count,
            parent_artifact_id=old.parent_artifact_id,
            status=ArtifactStatus.SUPERSEDED,
        )
        self.lake.write(
            old.table,
            [
                {
                    "artifact_id": old.artifact_id,
                    "artifact_status": ArtifactStatus.SUPERSEDED.value,
                    "superseded_by": new_artifact_id,
                }
            ],
        )
        return superseded

    def needs_gpu(self, kind: TaskKind) -> bool:
        """该种类是否要进 GPU 池。"""
        return engine_for(kind).needs_gpu

    # ---- GPU 准入（原文第五章：推理引擎按优先级抢 GPU）----

    def acquire_gpu(self, envelope: TaskEnvelope, *, now: datetime | None = None) -> GpuAdmission:
        """替一个 GPU 任务向共享池要卡：入队 → 跑一轮调度 → 看自己有没有拿到租约。

        不吃 GPU 的任务直接放行（``granted=True``、无租约），这样调用方不必自己分类。
        拿不到卡不抛异常——任务留在池子里排队，再调一次本方法即可重试。
        """
        moment = now or datetime.now()
        if not self.needs_gpu(envelope.kind):
            return GpuAdmission(True, None, "")
        self.gpu_pool.offer(envelope)
        self.gpu_pool.schedule(moment)
        lease = self.gpu_pool.lease_for(envelope.task_id)
        if lease is not None:
            return GpuAdmission(True, lease, "")
        window = current_window(moment)
        if not fits_window(envelope.kind, window):
            reason = (
                f"{envelope.kind.value} 不在当前窗口（{window.value}）跑，"
                f"排队等窗口——时间错峰避免资源争抢"
            )
        else:
            reason = (
                f"窗口正确但无空闲卡，按优先级排队（优先级 {envelope.priority}，"
                f"队列深度 {len(self.gpu_pool.queued_task_ids())}）"
            )
        return GpuAdmission(False, None, reason)

    def release_gpu(self, task_id: str) -> bool:
        """跑完交卡。被抢占的任务池子已经收过卡了，这里返回 False。"""
        return self.gpu_pool.release(task_id)

    def was_preempted(self, task_id: str) -> bool:
        """任务是否在跑的过程中被抢占了（峰值期高优任务抢占 / 缩容驱逐）。

        执行侧据此决定下一轮是从头跑还是从 checkpoint 续跑
        （见 :mod:`.ray_engine`——这正是选 Ray + vLLM 而不是 Triton 的理由）。
        """
        return self.gpu_pool.is_preempted(task_id)


def _default_signature(envelope: TaskEnvelope, result: ExecutionResult) -> str:
    """默认内容指纹：输入选择器 + 规则版本 + 目标表 + 行数。

    ⚠️ 原文未明确，本项目设计：原文没讲 content_hash 的原料。这里用「决定产出内容的
    全部输入」，保证同样输入跑出同样 artifact_id（重试幂等），不同输入必然不同。
    子系统可用 ``ExecutionResult.content_signature`` 覆盖成更精确的指纹。
    """
    return "|".join(
        [
            envelope.kind.value,
            envelope.input_selector,
            str(envelope.rule_id),
            str(envelope.rule_version),
            result.target_table,
            result.data_id,
            str(len(result.rows)),
        ]
    )


# --------------------------------------------------------------------------- 适配器骨架


class BaseSubsystemAdapter(ABC):
    """给 mining / sampling / tags 复用的适配器骨架。

    子系统在自己的 ``plane_adapter.py`` 里这样接入（接口约定见
    :mod:`adas_lakehouse.controlplane.subsystems`）::

        from adas_lakehouse.dataplane.execution import BaseSubsystemAdapter, ExecutionResult

        class MiningAdapter(BaseSubsystemAdapter):
            name = "mining"
            kinds = frozenset({TaskKind.RULE_MINING, TaskKind.VLM_INFERENCE})

            def run(self, ctx):
                ...  # 真正去 Spark / Ray 上跑，返回 [ExecutionResult, ...]

        def build_plane_adapter():
            return MiningAdapter()

    骨架负责的是与控制面打交道的那部分：句柄管理、幂等（同 run_id 不重复跑）、
    状态回报、异常转 FAILED。子系统只需实现 :meth:`run`。

    ⚠️ 原文未明确，本项目设计：这套接入骨架是本项目定的工程约定，原文只在架构层面
    要求「引擎与服务解耦、引擎之间也解耦」。
    """

    #: 子系统名，须与 controlplane.subsystems.SUBSYSTEM_MODULE_PATHS 的键一致
    name: str = ""
    #: 本子系统接的任务种类
    kinds: frozenset[TaskKind] = frozenset()
    #: 算法版本，进 artifact_id
    algo_version: str = "v1"

    def __init__(self, data_plane: DataPlane | None = None) -> None:
        if not self.name:
            raise ValueError("适配器必须声明 name")
        self.data_plane = data_plane or DataPlane()
        self._lock = threading.RLock()
        self._by_handle: dict[str, RunReport] = {}
        self._by_run: dict[str, str] = {}
        #: 句柄 -> 还卡在 GPU 队列里、等下一轮准入的信封
        self._awaiting_gpu: dict[str, TaskEnvelope] = {}

    # ---- 子系统实现这个 ----

    @abstractmethod
    def run(self, ctx: ExecutionContext) -> list[ExecutionResult]:
        """在数据面真正跑一次计算，返回要落湖仓的结果。

        实现方注意：读数据请自己连湖仓（走 ``config.settings()`` 拿连接信息），
        **不要**指望信封里带数据——信封里只有谓词。
        """

    # ---- 控制面接口约定（SubsystemAdapter 协议）----

    def supported_kinds(self) -> frozenset[TaskKind]:
        return self.kinds

    def submit(self, envelope: TaskEnvelope) -> str:
        """同步执行并返回句柄。幂等：同一 run_id 重复提交返回同一句柄，不跑第二遍。

        吃 GPU 的任务（VLM 推理 / Embedding）先过一道**GPU 准入**：抢到卡才开跑，
        没抢到就挂在池子的优先级队列里，本次回报 ``RUNNING``，等控制面下一次
        :meth:`poll` 再试——这就是原文第五章「推理任务白天按优先级队列执行、
        Embedding 走凌晨窗口」在执行侧的落点。**排队不是失败**，所以不回 FAILED。

        ⚠️ 原文未明确，本项目设计：骨架默认**同步**执行，因为这样最容易验证正确性。
        真实引擎（Spark / Ray）应覆写本方法改为异步提交并立刻返回外部作业 ID。
        """
        with self._lock:
            existing = self._by_run.get(envelope.run_id)
            if existing is not None and existing not in self._awaiting_gpu:
                return existing
        if envelope.kind not in self.kinds:
            raise ValueError(f"子系统 {self.name} 不接任务种类 {envelope.kind.value}")
        handle = f"{self.name}:{envelope.run_id}"
        self._attempt(envelope, handle)
        return handle

    def poll(self, handle: str) -> RunReport:
        """查作业状态。还在 GPU 队列里的任务，每次 poll 都顺手再抢一次卡。"""
        with self._lock:
            waiting = self._awaiting_gpu.get(handle)
        if waiting is not None:
            return self._attempt(waiting, handle)
        with self._lock:
            report = self._by_handle.get(handle)
        if report is None:
            raise KeyError(f"未知作业句柄: {handle!r}")
        return report

    def cancel(self, handle: str) -> bool:
        """取消作业。

        还在 GPU 队列里排队的任务是**真能取消**的——把它从池子里摘掉，返回 True，
        否则被取消的任务还占着队列位置，别人排在它后面白等。已经同步跑完的没有在途
        作业可取消，返回 False。

        异步实现（Spark / Ray）应覆写本方法，去真正 kill 外部作业。
        """
        with self._lock:
            waiting = self._awaiting_gpu.pop(handle, None)
        if waiting is None:
            return False
        self.data_plane.gpu_pool.cancel(waiting.task_id)
        return True

    # ---- 内部：一次执行尝试 ----

    def _now(self) -> datetime:
        """当前时刻。单独成方法，好让「凌晨窗口 / 峰值期」这类与时间强相关的行为
        可以在测试里被确定性地驱动，而不必去 patch 全局 ``datetime``。"""
        return datetime.now()

    def _attempt(self, envelope: TaskEnvelope, handle: str) -> RunReport:
        """跑一次（或再排一轮队）。GPU 准入 → run() → 写湖仓 → 交卡。"""
        started_at = self._now()
        gpu = self.data_plane.needs_gpu(envelope.kind)
        if gpu:
            admission = self.data_plane.acquire_gpu(envelope, now=started_at)
            if not admission.granted:
                report = RunReport(
                    run_id=envelope.run_id,
                    task_id=envelope.task_id,
                    state=TaskState.RUNNING,
                    engine=f"{engine_for(envelope.kind).name_cn}（等 GPU）",
                    started_at=started_at,
                    message=admission.reason,
                    metrics={"progress_percent": 10, **admission.as_metrics()},
                )
                with self._lock:
                    self._awaiting_gpu[handle] = envelope
                    self._by_handle[handle] = report
                    self._by_run[envelope.run_id] = handle
                return report
        ctx = ExecutionContext(
            envelope=envelope,
            engine=engine_for(envelope.kind),
            started_at=started_at,
            algo_version=self.algo_version,
        )
        try:
            results = self.run(ctx)
            report = self.data_plane.execute(
                envelope, results, algo_version=self.algo_version, started_at=ctx.started_at
            )
        except Exception as exc:
            _log.exception("子系统 %s 执行任务 %s 失败", self.name, envelope.task_id)
            report = RunReport(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                state=TaskState.FAILED,
                engine=self.name,
                started_at=ctx.started_at,
                finished_at=datetime.now(),
                message=f"{type(exc).__name__}: {exc}"[:500],
            )
        finally:
            if gpu:
                self.data_plane.release_gpu(envelope.task_id)
        with self._lock:
            self._awaiting_gpu.pop(handle, None)
            self._by_handle[handle] = report
            self._by_run[envelope.run_id] = handle
        return report

    def health(self) -> bool:
        """默认健康。子系统可覆写，去探引擎与湖仓连通性。"""
        return True


#: 供外部快速核对的常量转发：向量化的凌晨截止时间与在线服务规格。
EMBEDDING_DEADLINE_HOUR = K.EMBEDDING_WINDOW_DEADLINE_HOUR
