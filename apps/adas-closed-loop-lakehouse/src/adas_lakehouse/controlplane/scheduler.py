"""控制面编排器：任务提交 → 准入 → 排队 → 下发 → 轮询 → 审核 → 回写。

这是本子系统的门面类 :class:`ControlPlane`。它把前面几个模块串起来，对外只暴露
七个动作：``submit`` / ``dispatch_once`` / ``poll_once`` / ``review`` / ``cancel`` /
``progress`` / ``rebuild_check``。

它做什么（原文第四章「控制面（平台本地）」）：
  · 编排任务：决定谁先跑、跑几次、跑失败了要不要重试；
  · 下发规则：把规则版本冻结进任务信封；
  · 管理状态：任务执行状态 + 审核流状态，全部落控制面存储。

它**不**做什么：
  · 不读一行主数据——输入是 SQL 谓词，不是数据；
  · 不写一行主数据——产物由数据面直接写湖仓，控制面只收 artifact_id；
  · 不 import 任何子系统的内部实现——只经 :class:`~.subsystems.SubsystemRegistry`。

关键数字：单轮下发上限 :data:`~.constants.DISPATCH_BATCH_SIZE`、
最大自动重试 :data:`~.constants.MAX_AUTO_RETRY`、
轮询间隔 :data:`~.constants.POLL_INTERVAL_SECONDS`（均为 ⚠️ 本项目设计）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..ids import new_run_id
from . import constants as K
from . import lifecycle
from .audit import SchemaAudit, audit_control_plane_schema
from .contracts import (
    CONTROL_PLANE_ASSETS,
    DATA_PLANE_ASSETS,
    Plane,
    ReviewDecision,
    RunReport,
    TaskEnvelope,
    TaskKind,
    TaskRecord,
    TaskState,
    asset_plane,
)
from .rules import RuleConfig, RuleRegistry
from .store import (
    ControlPlaneStore,
    HotCache,
    InMemoryControlPlaneStore,
    InMemoryHotCache,
)
from .subsystems import SubsystemRegistry, SubsystemUnavailable, subsystem_for

__all__ = ["ControlPlane", "SubmitRequest", "DispatchOutcome", "RebuildCheck"]

_log = logging.getLogger(__name__)

#: 幂等键在 Redis 里的前缀。⚠️ 原文未明确，本项目设计。
_IDEMPOTENCY_PREFIX = "cp:idem:"

#: 任务种类 -> artifact_id / run_id 里的 stage 段。
#: stage 段进 ID 字符串，所以必须短、小写、稳定（见 ids 模块的正则约束）。
#: ⚠️ 原文未明确，本项目设计：原文没有规定各引擎的 stage 命名。
_STAGE_BY_KIND: dict[TaskKind, str] = {
    TaskKind.FRAME_SAMPLING: "sampling",
    TaskKind.RULE_MINING: "mining",
    TaskKind.VLM_INFERENCE: "vlm",
    TaskKind.EMBEDDING: "embedding",
    TaskKind.TAG_GOVERNANCE: "tagging",
    TaskKind.CURATION: "curation",
}

#: 状态 -> 进度百分比。⚠️ 原文未明确，本项目设计（原文只给了进度查询接口）。
_PROGRESS_BY_STATE: dict[TaskState, int] = {
    TaskState.DRAFT: 0,
    TaskState.SUBMITTED: 5,
    TaskState.QUEUED: 10,
    TaskState.DISPATCHED: 20,
    TaskState.RUNNING: 50,
    TaskState.AWAITING_REVIEW: 90,
    TaskState.SUCCEEDED: 100,
    TaskState.FAILED: 100,
    TaskState.REJECTED: 100,
    TaskState.CANCELLED: 100,
}


@dataclass(frozen=True, slots=True)
class SubmitRequest:
    """一次任务提交请求（来自 OpenAPI 任务类接口或控制台）。

    :param idempotency_key: 幂等键，原文第六章「幂等键防重复提交」。
        不传则由 ``(kind, rule_id, rule_version, input_selector, params)`` 算稳定指纹。
    """

    kind: TaskKind
    input_selector: str = ""
    input_tables: tuple[str, ...] = ()
    params: Mapping[str, Any] | None = None
    rule_id: str | None = None
    rule_version: int | None = None
    priority: int = K.PRIORITY_DEFAULT
    requested_by: str = "system"
    idempotency_key: str | None = None

    def fingerprint(self) -> str:
        """稳定指纹：同样的请求算出同一个键，天然防重复提交。"""
        payload = json.dumps(
            {
                "kind": self.kind.value,
                "input_selector": self.input_selector,
                "input_tables": sorted(self.input_tables),
                "params": dict(self.params or {}),
                "rule_id": self.rule_id,
                "rule_version": self.rule_version,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        from ..ids import content_hash

        return f"{self.kind.value}:{content_hash(payload, length=16)}"


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """一轮调度的结果，供运维观测。"""

    dispatched: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()  # (task_id, 原因)
    failed: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "dispatched": list(self.dispatched),
            "skipped": [{"task_id": t, "reason": r} for t, r in self.skipped],
            "failed": [{"task_id": t, "reason": r} for t, r in self.failed],
        }


@dataclass(frozen=True, slots=True)
class RebuildCheck:
    """控制面重建自检结果。对应原文第四章 💡 判断标准。"""

    criterion: str
    control_plane_assets_lost: tuple[str, ...]
    data_plane_assets_intact: tuple[str, ...]
    passed: bool
    detail: str
    #: 是否真的清了库（:meth:`ControlPlane.rebuild_drill`），而不只是静态断言
    drill_executed: bool = False
    #: 清库前后数据面的探针读数。两者必须相等，否则清控制面把业务数据一起带走了
    data_plane_before: int | None = None
    data_plane_after: int | None = None
    #: 清库带走的控制面任务行数——这个数越大越说明控制面确实只是运行态
    control_rows_dropped: int = 0
    #: 模式层自检结论（审控制面建表语句本身）。``None`` 表示这次没审模式层。
    schema_audit: SchemaAudit | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "control_plane_assets_lost": list(self.control_plane_assets_lost),
            "data_plane_assets_intact": list(self.data_plane_assets_intact),
            "passed": self.passed,
            "detail": self.detail,
            "drill_executed": self.drill_executed,
            "data_plane_before": self.data_plane_before,
            "data_plane_after": self.data_plane_after,
            "control_rows_dropped": self.control_rows_dropped,
            "schema_audit": self.schema_audit.as_dict() if self.schema_audit else None,
        }


class ControlPlane:
    """控制面编排器。

    :param store: 控制面状态存储，默认进程内实现
    :param cache: 热缓存（检索热点 / 字典 / 幂等键），默认进程内实现
    :param subsystems: 子系统注册表，默认按模块路径延迟绑定
    :param rules: 规则注册表
    """

    def __init__(
        self,
        *,
        store: ControlPlaneStore | None = None,
        cache: HotCache | None = None,
        subsystems: SubsystemRegistry | None = None,
        rules: RuleRegistry | None = None,
    ) -> None:
        self.store: ControlPlaneStore = store or InMemoryControlPlaneStore()
        self.cache: HotCache = cache or InMemoryHotCache()
        self.subsystems = subsystems or SubsystemRegistry()
        self.rules = rules or RuleRegistry()

    # ------------------------------------------------------------------ 提交

    def submit(self, request: SubmitRequest) -> TaskRecord:
        """提交任务。幂等：同一个幂等键重复提交直接返回已有任务，不会跑第二遍。

        流程：算幂等键 → 查重 → 组装信封（主数据守卫在信封构造里）→ 落库 →
        DRAFT → SUBMITTED → QUEUED。

        :raises SubsystemUnavailable: 任务种类没有对应子系统
        :raises MasterDataLeak: 请求参数里夹带了主数据
        """
        key = request.idempotency_key or request.fingerprint()
        existing = self.store.find_by_idempotency_key(key)
        if existing is not None:
            _log.info("幂等命中：任务 %s 已存在，状态 %s", existing.task_id, existing.state.value)
            return existing
        # Redis SETNX 做第二道闸：多副本网关并发提交时防重复（在线服务区每服务 ≥ 2 副本）
        if not self.cache.set_if_absent(
            _IDEMPOTENCY_PREFIX + key, "1", K.IDEMPOTENCY_KEY_TTL_SECONDS
        ):
            again = self.store.find_by_idempotency_key(key)
            if again is not None:
                return again

        subsystem = subsystem_for(request.kind)
        params = dict(request.params or {})
        rule: RuleConfig | None = None
        if request.rule_id:
            rule = self.rules.get(request.rule_id, request.rule_version)
            params.setdefault("rule", rule.dispatch_params())

        run_id = str(new_run_id(_STAGE_BY_KIND[request.kind]))
        task_id = f"task_{request.kind.value}_{uuid.uuid4().hex[:12]}"
        envelope = TaskEnvelope(
            task_id=task_id,
            kind=request.kind,
            subsystem=subsystem,
            run_id=run_id,
            input_selector=request.input_selector or (rule.where_clause() if rule else ""),
            input_tables=request.input_tables or (rule.input_tables if rule else ()),
            params=params,
            rule_id=request.rule_id,
            rule_version=rule.rule_version if rule else request.rule_version,
            priority=request.priority,
            requested_by=request.requested_by,
            idempotency_key=key,
        )
        record = TaskRecord(envelope=envelope, state=TaskState.DRAFT)
        self.store.save_task(record)

        record = self._advance(
            record, TaskState.SUBMITTED, actor=request.requested_by, detail="OpenAPI 提交"
        )
        record = self._advance(record, TaskState.QUEUED, detail="准入通过，进入控制面队列")
        return record

    # ------------------------------------------------------------------ 下发

    def dispatch_once(self, *, limit: int = K.DISPATCH_BATCH_SIZE) -> DispatchOutcome:
        """把队列里的任务下发给数据面子系统。

        按优先级升序取任务（优先级数值越小越优先），单轮最多
        :data:`~.constants.DISPATCH_BATCH_SIZE` 条。某个子系统不可用时，
        只跳过它的任务，其他链路照常——「任何一个引擎故障，都不影响其他链路」。
        """
        dispatched: list[str] = []
        skipped: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []

        for record in self.store.list_tasks(states=[TaskState.QUEUED], limit=limit):
            try:
                adapter = self.subsystems.resolve_for(record.kind)
            except SubsystemUnavailable as exc:
                skipped.append((record.task_id, str(exc)))
                continue
            try:
                if not adapter.health():
                    skipped.append((record.task_id, f"子系统 {record.subsystem} 健康检查未通过"))
                    continue
                handle = adapter.submit(record.envelope)
            except Exception as exc:  # 下发失败 → FAILED，等重试循环捞
                _log.warning("任务 %s 下发失败：%r", record.task_id, exc)
                self._advance(
                    record, TaskState.FAILED, detail=f"下发失败：{exc!r}", message=str(exc)
                )
                failed.append((record.task_id, repr(exc)))
                continue
            self._advance(
                record,
                TaskState.DISPATCHED,
                actor=record.subsystem,
                detail=f"已下发，作业句柄 {handle}",
                external_handle=handle,
            )
            dispatched.append(record.task_id)

        return DispatchOutcome(tuple(dispatched), tuple(skipped), tuple(failed))

    # ------------------------------------------------------------------ 轮询

    def poll_once(self, *, limit: int = K.DISPATCH_BATCH_SIZE) -> list[TaskRecord]:
        """轮询在途任务的状态，把数据面回报翻译成控制面状态。

        轮询间隔由调用方按 :data:`~.constants.POLL_INTERVAL_SECONDS` 控制。
        超过 :data:`~.constants.TASK_TIMEOUT_SECONDS` 仍无进展的任务直接判 FAILED。
        """
        updated: list[TaskRecord] = []
        in_flight = self.store.list_tasks(
            states=[TaskState.DISPATCHED, TaskState.RUNNING], limit=limit
        )
        for record in in_flight:
            if lifecycle.is_timed_out(record):
                updated.append(
                    self._advance(
                        record,
                        TaskState.FAILED,
                        detail=f"超时 {K.TASK_TIMEOUT_SECONDS} 秒无进展",
                        message="timeout",
                    )
                )
                continue
            if not record.external_handle:
                continue
            try:
                adapter = self.subsystems.resolve(record.subsystem)
                report = adapter.poll(record.external_handle)
            except Exception as exc:
                _log.warning("任务 %s 轮询失败：%r", record.task_id, exc)
                continue
            updated_record = self.accept_report(record, report)
            if updated_record is not None:
                updated.append(updated_record)
        return updated

    def accept_report(self, record: TaskRecord, report: RunReport) -> TaskRecord | None:
        """接收数据面的运行回报并落状态。回报内容已在 RunReport 构造时过了主数据守卫。

        :return: 状态有变化时返回新记录，否则 None
        """
        if report.run_id != record.envelope.run_id:
            raise ValueError(
                f"回报的 run_id {report.run_id} 与任务 {record.task_id} 的 "
                f"{record.envelope.run_id} 不符——血缘对不上，拒绝接收"
            )
        target = lifecycle.next_state_after_run(record, report.state)
        changes: dict[str, Any] = {
            "artifacts": report.artifacts or record.artifacts,
            "rows_written": report.rows_written or record.rows_written,
            "message": report.message or record.message,
        }
        if target is record.state:
            # 状态没变，但产物/行数可能更新了——仍要落库，进度接口要读
            refreshed = record.evolve(**changes)
            self.store.save_task(refreshed)
            return None
        return self._advance(
            record,
            target,
            actor=record.subsystem,
            detail=f"数据面回报 {report.state.value}，引擎 {report.engine or '-'}",
            **changes,
        )

    # ------------------------------------------------------------------ 审核流

    def review(
        self, task_id: str, decision: ReviewDecision, *, reviewer: str, note: str = ""
    ) -> TaskRecord:
        """审核裁决。原文第六章标签治理类接口：「候选审核 approve / reject」。

        审核流状态是控制面三类运行态之一，裁决结果同样会回写 ``dwd_mining_task_detail``。
        """
        record = self._require(task_id)
        seq = self.store.next_event_seq(task_id)
        new_record, event = lifecycle.apply_review(
            record, decision, seq=seq, reviewer=reviewer, detail=note
        )
        self.store.save_task(new_record)
        self.store.append_event(event)
        return new_record

    # ------------------------------------------------------------------ 取消与重试

    def cancel(self, task_id: str, *, actor: str = "system", reason: str = "") -> TaskRecord:
        """取消任务。已下发的会顺带通知子系统取消外部作业（失败不阻断状态迁移）。"""
        record = self._require(task_id)
        if record.external_handle:
            try:
                self.subsystems.resolve(record.subsystem).cancel(record.external_handle)
            except Exception as exc:  # 子系统取消失败不该让控制面卡死
                _log.warning("任务 %s 的外部作业取消失败：%r", task_id, exc)
        return self._advance(record, TaskState.CANCELLED, actor=actor, detail=reason or "人工取消")

    def retry_failed(self, *, limit: int = K.DISPATCH_BATCH_SIZE) -> list[str]:
        """把失败且未超重试上限的任务重新入队。

        重试安全的根据：主数据在湖仓、artifact_id 由内容哈希决定，同样输入跑出同样 ID，
        重跑不会产生重复数据（见 ids 模块「重试天然幂等」）。
        """
        retried: list[str] = []
        for record in self.store.list_tasks(states=[TaskState.FAILED], limit=limit):
            if record.attempt >= K.MAX_AUTO_RETRY:
                continue
            try:
                self._advance(
                    record,
                    TaskState.QUEUED,
                    detail=f"自动重试第 {record.attempt + 1} 次（上限 {K.MAX_AUTO_RETRY}）",
                )
                retried.append(record.task_id)
            except lifecycle.RetryExhausted:
                continue
        return retried

    # ------------------------------------------------------------------ 查询

    def progress(self, task_id: str) -> dict[str, Any]:
        """任务进度，服务于 ``GET /jobs/{jobId}/progress``（原文第六章任务类接口）。

        返回里只有 ID 与计数，没有任何主数据——检索明细要走数据面的双路查询出口。
        """
        record = self._require(task_id)
        percent = _PROGRESS_BY_STATE[record.state]
        return {
            "jobId": record.task_id,
            "runId": record.envelope.run_id,
            "kind": record.kind.value,
            "subsystem": record.subsystem,
            "state": record.state.value,
            "progressPercent": percent,
            "attempt": record.attempt,
            "rowsWritten": record.rows_written,
            "artifactIds": [a.artifact_id for a in record.artifacts],
            "reviewDecision": record.review_decision.value if record.review_decision else None,
            "message": record.message,
            "updatedAt": record.updated_at.isoformat(timespec="seconds"),
        }

    def timeline(self, task_id: str) -> list[dict[str, Any]]:
        """任务的完整状态迁移审计流——原文「平台的每一步操作都进血缘」的那一串步。"""
        return [e.as_row() for e in self.store.list_events(task_id)]

    def queue_depth(self) -> dict[str, int]:
        """各状态任务数，供在线服务区的 HPA 与运维看板。"""
        counts: dict[str, int] = {s.value: 0 for s in TaskState}
        for record in self.store.list_tasks(limit=10_000):
            counts[record.state.value] += 1
        return counts

    # ------------------------------------------------------------------ 健康判据

    def rebuild_check(self) -> RebuildCheck:
        """执行原文第四章的健康判据：把平台的数据库清空重建，业务数据是否完好？

        本方法做的是**静态断言**，不真的清库；判据分两层：

        资产层
            控制面持有的资产清单里有没有混进数据面主数据。
            ⚠️ 这一层**自我指涉、恒为真**：:func:`~.contracts.asset_plane` 判归属的
            依据就是「在不在 ``CONTROL_PLANE_ASSETS`` 里」，所以遍历该字典再问它归谁，
            答案只能是控制面。保留它是为了兼容既有返回结构与调用方，
            但它证明不了任何事——真正的判据在下一层。

        模式层（:func:`~.audit.audit_control_plane_schema`）
            审**真正会落盘**的东西：控制面 MySQL 建表语句的表清单与列名，
            以及数据面资产能不能锚到 registry 登记在册的湖仓表上。多一张未登记的表、
            多一列 ``image_embedding``、表名影射了湖仓表，这一层都会直接判不通过。

        两层都过才 ``passed=True``。真要演练清库用 :meth:`rebuild_drill`。
        """
        leaked = [a for a in CONTROL_PLANE_ASSETS if asset_plane(a) is not Plane.CONTROL]
        intact = tuple(DATA_PLANE_ASSETS)
        schema = audit_control_plane_schema()
        passed = not leaked and schema.passed
        if leaked:
            detail = f"控制面持有了数据面资产：{leaked}——违反第一设计原则「{K.FIRST_PRINCIPLE}」"
        elif not schema.passed:
            detail = "控制面持久化模式自检未通过：" + "；".join(f.detail for f in schema.findings)
        else:
            detail = (
                "控制面只持有运行态（规则配置 / 任务配置与执行状态 / 审核流状态 / 热缓存），"
                f"落盘只有 {schema.table_count} 张 cp_ 表 / {schema.column_count} 列，"
                "无一列命中主数据黑名单；主数据（clip / 图片 / 标签 / 向量）全部在 Paimon，"
                f"{len(schema.data_plane_anchors)} 类数据面资产逐一锚定到 registry 登记的湖仓表；"
                "清空控制面后重建配置即可恢复，业务数据完好。"
            )
        return RebuildCheck(
            criterion=K.HEALTH_CRITERION,
            control_plane_assets_lost=tuple(CONTROL_PLANE_ASSETS),
            data_plane_assets_intact=intact,
            passed=passed,
            detail=detail,
            schema_audit=schema,
        )

    def rebuild_drill(self, data_plane_probe: Callable[[], int] | None = None) -> RebuildCheck:
        """**真的演练一次**原文第四章的健康判据，而不只是静态断言。

        步骤就是原文那句话的字面执行：

          1. 读一次数据面探针（湖仓里现在有多少行业务数据）；
          2. ``store.purge()``——**把平台的数据库清空**；
          3. 再读一次探针，确认**业务数据完好**（前后相等）；
          4. 确认控制面确实被清空了（清干净才算数，不然演练没发生）。

        判据通过的充要条件：数据面读数不变 **且** 控制面确实清空了 **且** 静态资产
        归属检查也通过。任一不满足即 ``passed=False``。

        :param data_plane_probe: 数据面行数探针，通常是一句
            ``SELECT count(*) FROM dwd_mining_image_frame_detail``。
            不传则用常量 0——此时演练只能证明「清库这个动作本身没碰数据面」，
            证不了湖仓里的数据还在，``detail`` 会写明这一点。
        :raises RuntimeError: 演练后控制面仍有残留任务（purge 实现有问题）
        """
        probe = data_plane_probe or (lambda: 0)
        before = probe()
        dropped = len(self.store.list_tasks(limit=10_000))
        self.store.purge()
        after = probe()
        remaining = len(self.store.list_tasks(limit=10_000))
        if remaining:
            raise RuntimeError(
                f"演练失败：purge() 之后控制面还剩 {remaining} 条任务，"
                f"「清空重建」这一步没真的发生，判据无从谈起"
            )
        static = self.rebuild_check()
        data_intact = before == after
        passed = static.passed and data_intact
        if not data_intact:
            detail = (
                f"清空控制面后数据面读数从 {before} 变成 {after}——"
                f"说明控制面持有了本该在湖仓的业务数据，违反第一设计原则「{K.FIRST_PRINCIPLE}」"
            )
        elif data_plane_probe is None:
            detail = (
                f"已清空控制面 {dropped} 条任务运行态，控制面资产归属检查通过；"
                f"未提供数据面探针，湖仓完好性未实测（传 data_plane_probe 可实测）"
            )
        else:
            detail = (
                f"已清空控制面 {dropped} 条任务运行态，数据面读数 {before} → {after} 不变："
                f"业务数据完好，控制面/数据面确已分离"
            )
        return RebuildCheck(
            criterion=K.HEALTH_CRITERION,
            control_plane_assets_lost=static.control_plane_assets_lost,
            data_plane_assets_intact=static.data_plane_assets_intact,
            passed=passed,
            detail=detail,
            drill_executed=True,
            data_plane_before=before,
            data_plane_after=after,
            control_rows_dropped=dropped,
        )

    # ------------------------------------------------------------------ 内部

    def _require(self, task_id: str) -> TaskRecord:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(f"未知任务: {task_id!r}")
        return record

    def _advance(
        self,
        record: TaskRecord,
        target: TaskState,
        *,
        actor: str = "system",
        detail: str = "",
        **changes: Any,
    ) -> TaskRecord:
        seq = self.store.next_event_seq(record.task_id)
        new_record, event = lifecycle.transition(
            record, target, seq=seq, actor=actor, detail=detail, **changes
        )
        self.store.save_task(new_record)
        self.store.append_event(event)
        return new_record


def iter_required_subsystems() -> Iterable[str]:
    """题面要求控制面必须能调度的三个子系统。"""
    from .subsystems import REQUIRED_SUBSYSTEMS

    return REQUIRED_SUBSYSTEMS
