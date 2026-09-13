"""冒烟：控制面——任务编排 / 规则下发 / 状态管理。

第一设计原则：**平台不持有主数据**。控制面只传指针（data_id / artifact_id / 表名），
不传 clip、图片、标签、向量本体。这条在下面被直接打靶。

主流程：submit → dispatch_once → poll_once → progress。
子系统适配器用进程内假实现，不 import 任何真实子系统。
"""

from __future__ import annotations

import pytest

from adas_lakehouse.controlplane import (
    ControlPlane,
    MasterDataLeak,
    RunReport,
    SubmitRequest,
    SubsystemRegistry,
    SubsystemUnavailable,
    TaskKind,
    TaskState,
    assert_no_master_data,
)
from adas_lakehouse.controlplane.rules import EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION

pytestmark = pytest.mark.smoke

RULE_ID = "RULE_RAIN_NIGHT_UNLIT_INTERSECTION"


class FakeMiningAdapter:
    """假的 mining 子系统。控制面只会调这五个方法，多一个都不会调。"""

    name = "mining"

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.handles: dict[str, str] = {}  # run_id -> handle
        self.envelopes: dict[str, object] = {}  # handle -> envelope
        self.cancelled: list[str] = []

    def supported_kinds(self):
        return frozenset({TaskKind.RULE_MINING})

    def submit(self, envelope):
        # 幂等：同一个 run_id 重复提交返回同一句柄，不跑第二遍
        handle = self.handles.setdefault(envelope.run_id, f"job-{len(self.handles) + 1}")
        self.envelopes[handle] = envelope
        return handle

    def poll(self, handle):
        env = self.envelopes[handle]
        return RunReport(
            run_id=env.run_id,
            task_id=env.task_id,
            state=TaskState.SUCCEEDED,
            engine="spark",
            rows_written=42,
        )

    def cancel(self, handle):
        self.cancelled.append(handle)
        return True

    def health(self):
        return self.healthy


def _plane(adapter: FakeMiningAdapter | None = None) -> tuple[ControlPlane, FakeMiningAdapter]:
    adapter = adapter or FakeMiningAdapter()
    registry = SubsystemRegistry()
    registry.register(adapter)
    plane = ControlPlane(subsystems=registry)
    plane.rules.upsert(EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION)
    return plane, adapter


def _submit(plane: ControlPlane, **overrides):
    payload = {"kind": TaskKind.RULE_MINING, "rule_id": RULE_ID}
    payload.update(overrides)
    return plane.submit(SubmitRequest(**payload))


# --------------------------------------------------------------------------- 主流程


def test_submit_dispatch_poll_reaches_succeeded():
    plane, adapter = _plane()

    task = _submit(plane)
    assert task.state is TaskState.QUEUED

    outcome = plane.dispatch_once()
    assert outcome.dispatched == (task.envelope.task_id,)
    assert outcome.failed == ()
    assert adapter.handles

    polled = plane.poll_once()
    assert [r.envelope.task_id for r in polled] == [task.envelope.task_id]

    progress = plane.progress(task.envelope.task_id)
    assert progress["state"] == TaskState.SUCCEEDED.value
    assert progress["progressPercent"] == 100
    assert progress["rowsWritten"] == 42


def test_task_timeline_records_every_transition():
    """状态流转要可回放，否则「这个任务当时卡在哪一步」永远查不清。"""
    plane, _ = _plane()
    task = _submit(plane)

    events = plane.timeline(task.envelope.task_id)
    assert len(events) >= 2
    assert events[0]["from_state"] == TaskState.DRAFT.value
    assert events[-1]["to_state"] == TaskState.QUEUED.value
    seqs = [e["event_seq"] for e in events]
    assert seqs == sorted(seqs)


def test_idempotency_key_collapses_duplicate_submissions():
    plane, _ = _plane()
    first = _submit(plane, idempotency_key="batch-2026-03-01")
    second = _submit(plane, idempotency_key="batch-2026-03-01")
    assert first.envelope.task_id == second.envelope.task_id
    assert plane.queue_depth()["queued"] == 1


def test_queue_depth_reports_every_state():
    plane, _ = _plane()
    depth = plane.queue_depth()
    assert set(depth) == {s.value for s in TaskState}
    assert sum(depth.values()) == 0

    _submit(plane)
    assert plane.queue_depth()["queued"] == 1


def test_cancel_moves_a_queued_task_to_cancelled():
    plane, _ = _plane()
    task = _submit(plane)
    plane.cancel(task.envelope.task_id)
    assert plane.progress(task.envelope.task_id)["state"] == TaskState.CANCELLED.value


# --------------------------------------------------------------------------- 平面边界


def test_master_data_may_not_ride_in_task_params():
    """第一设计原则：平台不持有主数据。向量、图片、标签本体一律不许进任务参数。"""
    assert_no_master_data({"data_id": "COLLECT_BP_20260301123045_b7e2"}, where="task params")

    for leak in ("embedding", "image_bytes", "point_cloud", "tag_values", "raw_payload"):
        with pytest.raises(MasterDataLeak, match=leak):
            assert_no_master_data({"data_id": "x", leak: [1, 2, 3]}, where="task params")


def test_run_report_carries_pointers_not_payloads():
    """回报只含指针（artifact_id / 行数），不含数据本体。"""
    _, adapter = _plane()
    plane, _ = _plane(adapter)
    task = _submit(plane)
    plane.dispatch_once()

    handle = next(iter(adapter.handles.values()))
    report = adapter.poll(handle)
    assert_no_master_data(report.metrics, where="run report metrics")
    assert report.rows_written == 42
    assert task is not None


# --------------------------------------------------------------------------- 子系统解耦


def test_missing_subsystem_only_breaks_its_own_lane():
    """任何一个子系统缺席，只让它那条链路报错，其他链路照跑。"""
    plane = ControlPlane(subsystems=SubsystemRegistry())  # 谁都没注册
    plane.rules.upsert(EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION)
    task = _submit(plane)

    outcome = plane.dispatch_once()  # 不抛异常

    assert outcome.dispatched == ()
    assert len(outcome.skipped) == 1
    skipped_task_id, reason = outcome.skipped[0]
    assert skipped_task_id == task.envelope.task_id
    assert "mining" in reason
    # 任务还在队列里等子系统上线，没被丢掉
    assert plane.queue_depth()["queued"] == 1


def test_registry_rejects_an_adapter_missing_the_protocol():
    class Incomplete:
        name = "mining"

        def health(self):
            return True

    with pytest.raises(SubsystemUnavailable):
        SubsystemRegistry().register(Incomplete())


def test_registry_rejects_an_unnamed_adapter():
    with pytest.raises(SubsystemUnavailable):
        SubsystemRegistry().register(object())


def test_resolving_an_unknown_subsystem_says_what_is_registered():
    with pytest.raises(SubsystemUnavailable, match="未知子系统"):
        SubsystemRegistry().resolve("no-such-subsystem")


def test_control_plane_import_does_not_import_any_subsystem():
    """延迟绑定：import 控制面不该把 mining / sampling / tags 一起拖进来。

    控制面代码里没有一行 ``import adas_lakehouse.mining``——这正是「任何一个子系统
    没写完 / 依赖没装都不会让控制面炸掉」的实现前提。开子进程验，才不受本测试
    进程里已经 import 过什么的影响。
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parents[2] / "src")
    env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    code = (
        "import sys, adas_lakehouse.controlplane;"
        "print([m for m in sys.modules if m.startswith('adas_lakehouse.')"
        " and m.split('.')[1] in ('mining', 'sampling', 'tags', 'vector', 'lineage')])"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    assert proc.stdout.strip() == "[]", f"控制面 import 时拖进了子系统: {proc.stdout}"
