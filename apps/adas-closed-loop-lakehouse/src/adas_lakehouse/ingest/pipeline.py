"""合规入湖链路的端到端编排：把五步链路、门禁、ODS 落地串成一个可调用入口。

[a8] 全文的工程收束点。一次调用完成：

    ① 车端脱敏 → ② 合规室上传 → ③ 合规脱密 → ④ 合规数据分发 → ⑤ 实时入湖
                                                                    └─ 四项门禁 ─┬─ 通过 → ods_data_file_meta
                                                                                 └─ 拒绝 → 五步异常闭环

用法::

    from adas_lakehouse.ingest import (
        ComplianceIngestPipeline, ComplianceCloudTopology, FileMeta, FileType,
    )

    pipeline = ComplianceIngestPipeline(
        topology=ComplianceCloudTopology(
            compliance_cloud_region="cn-shanghai", adas_cloud_region="cn-shanghai",
            compliance_cloud_vpc_id="vpc-adas-01", adas_cloud_vpc_id="vpc-adas-01",
        ),
    )
    outcome = pipeline.submit(meta, vehicle_operator="采集软件 v2.3", cloud_operator="XX 合规科技")
    outcome.accepted        # True / False
    outcome.chain.is_complete
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .channels import IngestReport, OssFileChannel
from .compliance import (
    DISTRIBUTION_PAYLOADS,
    ComplianceChain,
    ComplianceCloudTopology,
    ComplianceMarks,
    ComplianceStep,
    CrossBoundaryPayload,
)
from .errors import ComplianceViolation
from .gate import AnomalyClosedLoop, AnomalyRecord, Decision, GateOutcome, OssComplianceGate
from .oss import FileMeta, ObjectStore
from .sinks import InMemoryOdsSink, OdsSink

__all__ = ["IngestOutcome", "ComplianceIngestPipeline"]


@dataclass(slots=True)
class IngestOutcome:
    """一个采集文件走完合规入湖链路后的结果。"""

    file_id: str
    data_id: str
    chain: ComplianceChain
    gate: GateOutcome | None = None
    report: IngestReport | None = None
    anomaly: AnomalyRecord | None = None
    off_table_fields: tuple[str, ...] = ()
    error: str = ""

    @property
    def accepted(self) -> bool:
        return self.gate is not None and self.gate.decision is not Decision.REJECT

    def summary(self) -> str:
        if self.error:
            return f"{self.file_id}: 链路中断 — {self.error}"
        decision = self.gate.decision.value if self.gate else "未到门禁"
        step = self.chain.current_step
        return (
            f"{self.file_id} (data_id={self.data_id}) "
            f"链路至第 {step.value if step else 0} 步、门禁={decision}"
        )


class ComplianceIngestPipeline:
    """采集数据合规入湖链路的编排器。

    Args:
        topology: 合规云架构拓扑（独立云 / 同一 VPC / 不对外暴露），第 ④ 步校验用。
        store: 对象存储探针，交给门禁做 P1 物理校验；不传则不访问对象存储。
        sink: ODS 写出口，缺省内存 sink。
        closed_loop: 五步异常闭环执行器，缺省新建一个（隔离动作只留在内存）。
        gate: OSS 通道门禁，缺省按 store 构造。

    Note:
        本编排器负责的是**智驾云侧**能观测到的链路状态。第 ①~③ 步真实发生在车端、
        合规室与合规云，智驾云只能拿到「它们已完成」的标记与时间——这正是
        [a8] 的分界：智驾云从第一步就只接触脱敏后的数据。因此
        ``advance_processing_segment()`` 的语义是「登记这三步已完成」，
        而不是「在这里执行脱敏」。
    """

    def __init__(
        self,
        *,
        topology: ComplianceCloudTopology,
        store: ObjectStore | None = None,
        sink: OdsSink | None = None,
        closed_loop: AnomalyClosedLoop | None = None,
        gate: OssComplianceGate | None = None,
    ) -> None:
        self.topology = topology
        self.sink: OdsSink = sink if sink is not None else InMemoryOdsSink()
        self.closed_loop = closed_loop if closed_loop is not None else AnomalyClosedLoop()
        self.gate = gate if gate is not None else OssComplianceGate(store)
        self.channel = OssFileChannel(gate=self.gate, sink=self.sink, closed_loop=self.closed_loop)
        self.chains: dict[str, ComplianceChain] = {}

    # ---- 链路 ----

    def chain_for(self, data_id: str) -> ComplianceChain:
        """取（或新建）某个 clip 的链路实例。一个 data_id 一条链路。"""
        return self.chains.setdefault(data_id, ComplianceChain(data_id=data_id))

    def advance_processing_segment(
        self,
        data_id: str,
        marks: ComplianceMarks,
        *,
        vehicle_operator: str = "",
        compliance_room_operator: str = "",
        compliance_cloud_operator: str = "",
        moments: Mapping[ComplianceStep, datetime] | None = None,
    ) -> ComplianceChain:
        """登记「合规处理段」前三步已完成：车端脱敏 → 合规室上传 → 合规脱密。

        Raises:
            ComplianceViolation: 脱敏标记缺失或不完整。
        """
        chain = self.chain_for(data_id)
        moments = moments or {}
        chain.advance(
            ComplianceStep.VEHICLE_REDACTION,
            marks=marks,
            operator=vehicle_operator or (marks.vehicle.operator if marks.vehicle else ""),
            moment=moments.get(ComplianceStep.VEHICLE_REDACTION),
        )
        chain.advance(
            ComplianceStep.COMPLIANCE_ROOM_UPLOAD,
            operator=compliance_room_operator,
            moment=moments.get(ComplianceStep.COMPLIANCE_ROOM_UPLOAD),
        )
        chain.advance(
            ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION,
            marks=marks,
            operator=compliance_cloud_operator or (marks.cloud.operator if marks.cloud else ""),
            moment=moments.get(ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION),
        )
        return chain

    def distribute(
        self,
        data_id: str,
        *,
        moment: datetime | None = None,
        payloads: Sequence[CrossBoundaryPayload] = DISTRIBUTION_PAYLOADS,
    ) -> ComplianceChain:
        """第 ④ 步 · 合规数据分发：副本复制至智驾云 OSS + 向业务方 Kafka 发送文件元信息。

        这一步会强制校验合规云架构约束——同一云端、同一 VPC、不对外暴露——
        并逐个校验过境载荷：只有「合规数据副本」与「文件元信息」两类允许离开合规云，
        原始硬盘数据与未脱密数据的生命周期止步于合规云。

        Raises:
            ComplianceViolation: 架构约束不满足、载荷类型不许过境，或前置步骤未完成。
        """
        chain = self.chain_for(data_id)
        chain.advance(
            ComplianceStep.COMPLIANT_DATA_DISTRIBUTION,
            topology=self.topology,
            moment=moment,
            payloads=payloads,
        )
        return chain

    # ---- 入湖 ----

    def ingest(
        self,
        meta: FileMeta,
        *,
        ingest_time: datetime | None = None,
        reuse_completed_chain: bool = False,
    ) -> IngestOutcome:
        """第 ⑤ 步 · 实时入湖：过门禁、盖章、落 ``ods_data_file_meta``。

        被门禁拒绝的记录由通道内部推入五步异常闭环（拦截 → 隔离 → 告警），
        本方法把对应的 ``AnomalyRecord`` 一并带回。

        Args:
            reuse_completed_chain: 链路已走完第 ⑤ 步时是否允许**同一 clip 的另一个文件**
                继续入湖。缺省 False——此时重复推进第 ⑤ 步会抛 ``ComplianceViolation``，
                这是「可重放 ≠ 可重复入湖」的默认保守口径。
                置 True 用于 [a8]「一个采集任务数百个文件」的真实形态：一个 data_id
                （一个 clip ≈ 1 分钟片段）下挂着相机 / 点云 / IMU / GNSS 多个文件，
                它们共享同一条五步合规链路，链路只走一遍，文件逐个入湖。
        """
        chain = self.chain_for(meta.data_id)
        outcome = IngestOutcome(
            file_id=meta.file_id,
            data_id=meta.data_id,
            chain=chain,
            off_table_fields=meta.off_table_fields(),
        )
        try:
            chain.assert_ready_for_ingest()
        except ComplianceViolation as exc:
            outcome.error = str(exc)
            return outcome

        before = len(self.closed_loop.records)
        report = self.channel.ingest_meta(meta, ingest_time=ingest_time)
        outcome.report = report
        outcome.gate = (
            report.rejections[0] if report.rejections else self.gate.check(meta, row=meta.to_row())
        )
        if len(self.closed_loop.records) > before:
            outcome.anomaly = self.closed_loop.records[-1]
        if report.rejected == 0:
            already_ingested = ComplianceStep.REALTIME_INGEST in chain.completed
            if not (already_ingested and reuse_completed_chain):
                chain.advance(ComplianceStep.REALTIME_INGEST, moment=ingest_time)
        return outcome

    def submit(
        self,
        meta: FileMeta,
        *,
        vehicle_operator: str = "",
        cloud_operator: str = "",
        compliance_room_operator: str = "",
        ingest_time: datetime | None = None,
        reuse_completed_chain: bool = False,
    ) -> IngestOutcome:
        """一次调用走完五步：登记前三步 → 分发 → 入湖。

        脱敏标记优先用 ``meta.marks``；若 meta 未携带标记而调用方给了两个 operator，
        则按「双脱敏均已执行」构造标记（用于演练与补数）。

        Args:
            reuse_completed_chain: 见 ``ingest()``。同一 clip 的第二个文件要入湖时置 True。

        Returns:
            IngestOutcome。链路中断（合规违规）时 ``error`` 非空且 ``gate`` 为 None。
        """
        marks = meta.marks
        if not marks.is_complete and vehicle_operator and cloud_operator:
            marks = ComplianceMarks.both_applied(
                vehicle_operator=vehicle_operator, cloud_operator=cloud_operator
            )
            meta.marks = marks

        chain = self.chain_for(meta.data_id)
        try:
            if ComplianceStep.VEHICLE_REDACTION not in chain.completed:
                self.advance_processing_segment(
                    meta.data_id,
                    marks,
                    vehicle_operator=vehicle_operator,
                    compliance_room_operator=compliance_room_operator,
                    compliance_cloud_operator=cloud_operator,
                )
            if ComplianceStep.COMPLIANT_DATA_DISTRIBUTION not in chain.completed:
                self.distribute(meta.data_id, moment=meta.distributed_at)
        except ComplianceViolation as exc:
            return IngestOutcome(
                file_id=meta.file_id,
                data_id=meta.data_id,
                chain=chain,
                error=str(exc),
                off_table_fields=meta.off_table_fields(),
            )
        return self.ingest(
            meta, ingest_time=ingest_time, reuse_completed_chain=reuse_completed_chain
        )

    def submit_batch(
        self,
        metas: Iterable[FileMeta],
        *,
        reuse_completed_chain: bool = True,
        **kwargs: Any,
    ) -> list[IngestOutcome]:
        """批量提交（[a8]「一个采集任务数百个文件」的常见量级）。

        这里 ``reuse_completed_chain`` 缺省 **True** 而 ``submit()`` 缺省 False，
        因为批量提交本来就是多文件形态：一个采集任务里同一个 clip 会同时产出相机、
        点云、IMU、GNSS 多个文件，它们共享同一条五步合规链路（硬盘只被脱敏、上传、
        脱密、分发一次），链路不该因为第二个文件而被要求重走一遍，也不该在第二个
        文件上抛「不可重复推进」。逐条 ``submit()`` 的默认值保持保守不变。
        """
        return [
            self.submit(meta, reuse_completed_chain=reuse_completed_chain, **kwargs)
            for meta in metas
        ]

    # ---- 观测 ----

    def audit_rows(self) -> list[dict[str, Any]]:
        """导出所有链路的合规审计行，可落到闭环域的追溯表。"""
        return [chain.to_audit_row() for chain in self.chains.values()]

    def stats(self) -> dict[str, Any]:
        """链路总览：完成数 / 在途数 / 拦截数 / 合规问题数。"""
        completed = sum(1 for c in self.chains.values() if c.is_complete)
        compliance_issues = sum(
            1 for r in self.closed_loop.records if r.outcome.has_compliance_failure()
        )
        return {
            "chains": len(self.chains),
            "completed": completed,
            "in_flight": len(self.chains) - completed,
            "intercepted": len(self.closed_loop.records),
            "compliance_issues": compliance_issues,
        }

    def isolated_rows(self) -> Sequence[dict[str, Any]]:
        """隔离表待落库的行（拦截 → 隔离 的产物）。"""
        return [r.to_issue_row() for r in self.closed_loop.records]
