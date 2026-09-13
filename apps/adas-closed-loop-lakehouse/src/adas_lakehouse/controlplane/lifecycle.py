"""任务生命周期状态机：控制面对「执行状态」与「审核流状态」的唯一裁决处。

原文第四章只说了控制面 MySQL 存「任务配置与执行状态、审核流状态」，第六章给了
``GET /jobs/{jobId}/progress`` 查进度，但**没有给状态机**。

⚠️ 原文未明确，本项目设计：下面这张迁移表是本项目补的。设计约束有三条：
  1. 数据面只能把任务推进到 RUNNING / AWAITING_REVIEW / SUCCEEDED / FAILED——
     排队、下发、取消、审核裁决都是控制面的权力（见 contracts.RunReport 的状态白名单）；
  2. FAILED 可以回到 QUEUED 重试，重试次数上限 :data:`~.constants.MAX_AUTO_RETRY`；
     因为主数据在湖仓、控制面只有配置，重试天然幂等（artifact_id 由内容哈希决定）；
  3. 每一次迁移都生成一条 :class:`~.contracts.TaskEvent`，它就是原文说的
     「平台的每一步操作都进血缘」的那一步。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from . import constants as K
from .contracts import (
    REVIEW_REQUIRED_KINDS,
    TERMINAL_STATES,
    ReviewDecision,
    TaskEvent,
    TaskRecord,
    TaskState,
)

__all__ = [
    "TRANSITIONS",
    "IllegalTransition",
    "RetryExhausted",
    "allowed_next",
    "can_transition",
    "transition",
    "apply_review",
    "next_state_after_run",
    "is_timed_out",
]


class IllegalTransition(RuntimeError):
    """非法状态迁移。带上「当前态 → 目标态 → 合法目标集合」，方便定位调用方 bug。"""

    def __init__(self, task_id: str, src: TaskState, dst: TaskState) -> None:
        super().__init__(
            f"任务 {task_id} 不能从 {src.value} 迁移到 {dst.value}；"
            f"合法目标：{sorted(s.value for s in TRANSITIONS[src])}"
        )
        self.task_id = task_id
        self.src = src
        self.dst = dst


class RetryExhausted(RuntimeError):
    """重试次数用尽。"""


#: 状态迁移表。⚠️ 原文未明确，本项目设计（理由见模块 docstring）。
TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.DRAFT: frozenset({TaskState.SUBMITTED, TaskState.CANCELLED}),
    TaskState.SUBMITTED: frozenset({TaskState.QUEUED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.QUEUED: frozenset({TaskState.DISPATCHED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.DISPATCHED: frozenset(
        {
            TaskState.RUNNING,
            TaskState.SUCCEEDED,
            TaskState.AWAITING_REVIEW,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RUNNING: frozenset(
        {TaskState.AWAITING_REVIEW, TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.AWAITING_REVIEW: frozenset(
        {TaskState.SUCCEEDED, TaskState.REJECTED, TaskState.CANCELLED}
    ),
    # 终态：FAILED 可重新入队重试，其余不可再动
    TaskState.FAILED: frozenset({TaskState.QUEUED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.REJECTED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def allowed_next(state: TaskState) -> frozenset[TaskState]:
    """某状态的合法后继集合。"""
    return TRANSITIONS[state]


def can_transition(src: TaskState, dst: TaskState) -> bool:
    """判断迁移是否合法，不抛异常。"""
    return dst in TRANSITIONS[src]


def transition(
    record: TaskRecord,
    dst: TaskState,
    *,
    seq: int,
    actor: str = "system",
    detail: str = "",
    at: datetime | None = None,
    **changes: object,
) -> tuple[TaskRecord, TaskEvent]:
    """执行一次状态迁移，返回「新记录 + 审计事件」。

    :param record: 当前任务记录（不可变，原记录不被修改）
    :param dst: 目标状态
    :param seq: 该任务的事件序号，由 store 分配，保证审计流有序
    :param actor: 谁触发的（``system`` / 用户名 / 子系统名）
    :param detail: 附加说明，会写进审计事件
    :param at: 事件时间，默认 now
    :param changes: 顺带更新的记录字段（如 ``external_handle``、``artifacts``）
    :raises IllegalTransition: 目标状态不在迁移表里
    :raises RetryExhausted: FAILED → QUEUED 且重试次数已达 :data:`~.constants.MAX_AUTO_RETRY`
    """
    src = record.state
    if not can_transition(src, dst):
        raise IllegalTransition(record.task_id, src, dst)

    moment = at or datetime.now()
    if src is TaskState.FAILED and dst is TaskState.QUEUED:
        if record.attempt >= K.MAX_AUTO_RETRY:
            raise RetryExhausted(
                f"任务 {record.task_id} 已重试 {record.attempt} 次，达上限 {K.MAX_AUTO_RETRY}"
            )
        changes.setdefault("attempt", record.attempt + 1)

    # 执行时间打点：catalog 的 dwd_mining_task_detail 要求 start_time / end_time /
    # duration_sec 三件套，控制面是唯一知道这三个时刻的地方，所以在迁移处打点。
    # 重试（FAILED → QUEUED）会清掉上一轮的起止时刻，新一轮重新计时。
    if dst is TaskState.RUNNING and record.started_at is None:
        changes.setdefault("started_at", moment)
    if dst.is_terminal and record.finished_at is None:
        changes.setdefault("finished_at", moment)
    if src is TaskState.FAILED and dst is TaskState.QUEUED:
        changes["started_at"] = None
        changes["finished_at"] = None

    new_record = record.evolve(state=dst, updated_at=moment, **changes)
    # 状态变了就得重新回写数据面：血缘里必须看得到最新一步
    if new_record.written_back and dst is not src:
        new_record = new_record.evolve(written_back=False, updated_at=moment)

    event = TaskEvent(
        task_id=record.task_id,
        seq=seq,
        from_state=src,
        to_state=dst,
        at=moment,
        actor=actor,
        detail=detail,
    )
    return new_record, event


def next_state_after_run(record: TaskRecord, reported: TaskState) -> TaskState:
    """把数据面回报的状态翻译成控制面要落的状态。

    唯一的翻译规则：产出候选标签的任务（:data:`~.contracts.REVIEW_REQUIRED_KINDS`）
    即使数据面说 SUCCEEDED，控制面也只落 AWAITING_REVIEW——必须经审核流 approve
    才算成功。原文第六章：「候选审核 approve / reject……一律经统一标签服务收口」。
    """
    if reported is TaskState.SUCCEEDED and record.kind in REVIEW_REQUIRED_KINDS:
        return TaskState.AWAITING_REVIEW
    return reported


def apply_review(
    record: TaskRecord,
    decision: ReviewDecision,
    *,
    seq: int,
    reviewer: str,
    detail: str = "",
    at: datetime | None = None,
) -> tuple[TaskRecord, TaskEvent]:
    """审核裁决：approve → SUCCEEDED，reject → REJECTED。

    :raises IllegalTransition: 任务不在 AWAITING_REVIEW 状态
    """
    if record.state is not TaskState.AWAITING_REVIEW:
        raise IllegalTransition(
            record.task_id,
            record.state,
            TaskState.SUCCEEDED if decision is ReviewDecision.APPROVE else TaskState.REJECTED,
        )
    dst = TaskState.SUCCEEDED if decision is ReviewDecision.APPROVE else TaskState.REJECTED
    return transition(
        record,
        dst,
        seq=seq,
        actor=reviewer,
        detail=detail or f"审核 {decision.value}",
        at=at,
        review_decision=decision,
        reviewer=reviewer,
    )


def is_timed_out(record: TaskRecord, *, now: datetime | None = None) -> bool:
    """运行中的任务是否已超时。

    超时上限 :data:`~.constants.TASK_TIMEOUT_SECONDS`（⚠️ 原文未明确，本项目设计）。
    只对 DISPATCHED / RUNNING 生效——排队中的任务等多久都不算超时。
    """
    if record.state not in (TaskState.DISPATCHED, TaskState.RUNNING):
        return False
    elapsed = ((now or datetime.now()) - record.updated_at).total_seconds()
    return elapsed > K.TASK_TIMEOUT_SECONDS


def active_states() -> Iterable[TaskState]:
    """非终态集合，调度循环按它捞任务。"""
    return (s for s in TaskState if s not in TERMINAL_STATES)
