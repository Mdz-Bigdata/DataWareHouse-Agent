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

from .channels import ChannelKind, IngestReport, OssFileChannel, isolating_closed_loop
from .compliance import (
    DISTRIBUTION_PAYLOADS,
    ComplianceChain,
    ComplianceCloudTopology,
    ComplianceMarks,
    ComplianceStep,
    CrossBoundaryPayload,
)
from .errors import ComplianceViolation
from .gate import (
    AnomalyClosedLoop,
    AnomalyRecord,
    CheckResult,
    CheckStatus,
    Decision,
    GateCheck,
    GateOutcome,
    OssComplianceGate,
    Severity,
    TriageBranch,
)
from .oss import FileMeta, ObjectStore
from .sinks import InMemoryOdsSink, OdsSink

__all__ = ["IngestOutcome", "ComplianceIngestPipeline", "CHAIN_COMPLIANCE_CHECK"]

#: 合规链路中断时挂的那项检查。它不是 [a8] 第六章那四项专属检查之一（四项是**元信息**
#: 到了智驾云之后的门禁），而是「文件根本没走完五步链路」这件事本身。
#: 级别与归类取 [a8] 第六章对脱敏标记缺失的定性：P0，且**是合规问题不是数据质量问题**。
#: ⚠️ 检查编码是本项目起的，原文只给了文字描述。
CHAIN_COMPLIANCE_CHECK = GateCheck(
    "compliance_chain_incomplete",
    "五步合规链路未走完",
    "车端脱敏 → 合规室上传 → 合规脱密 → 合规数据分发 未全部完成，"
    "或合规云架构约束不满足、载荷不许离开合规云",
    Severity.P0,
    "完整性",
    is_compliance=True,
)


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
        topology: 合规云架构拓扑（独立云 / 同一 VPC / 不对外暴露），第 ②④ 步校验用。
        store: 对象存储探针，交给门禁做 P1 物理校验；不传则不访问对象存储。
        sink: **目标表** ``ods_data_file_meta`` 的写出口，缺省内存 sink。
        issue_sink: **隔离表** ``ods_quality_issue`` 的写出口，缺省另起一个内存 sink。
            两张表的出口分开是刻意的：``sink`` 里有多少行 = 有多少条数据真的入了湖，
            这个问题不该被隔离行搅浑。生产上两张表同属一个 Paimon catalog，
            把同一个 sink 传两遍（``sink=s, issue_sink=s``）即可。
        closed_loop: 五步异常闭环执行器。缺省按 ``isolating_closed_loop(issue_sink)``
            装配——被拦下的数据**真的写进** ``ods_quality_issue``，而不是只在内存里留个
            对象等调用方想起来落库（[a6] 第三章：「原始数据不丢失是整套门禁可重放、
            可审计的根基」）。
        gate: OSS 通道门禁，缺省按 store 与拓扑构造（拓扑里的合规云 bucket 会成为
            file_path 的禁入名单）。

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
        issue_sink: OdsSink | None = None,
        closed_loop: AnomalyClosedLoop | None = None,
        gate: OssComplianceGate | None = None,
    ) -> None:
        self.topology = topology
        self.sink: OdsSink = sink if sink is not None else InMemoryOdsSink()
        self.issue_sink: OdsSink = issue_sink if issue_sink is not None else InMemoryOdsSink()
        self.closed_loop = (
            closed_loop if closed_loop is not None else isolating_closed_loop(self.issue_sink)
        )
        self.gate = (
            gate
            if gate is not None
            else OssComplianceGate(
                store,
                allowed_buckets=(topology.adas_cloud_bucket,)
                if topology.adas_cloud_bucket
                else None,
                forbidden_buckets=(topology.compliance_cloud_bucket,),
            )
        )
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
            # 第 ② 步的目的地是「合规云对象存储（不对外暴露）」——把拓扑带进来，
            # 这条准入条件才是在第 ② 步生效，而不是等到第 ④ 步分发时才发现桶是敞开的。
            ComplianceStep.COMPLIANCE_ROOM_UPLOAD,
            topology=self.topology,
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
            return self._reject_on_compliance(meta, exc, outcome)

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
            return self._reject_on_compliance(
                meta,
                exc,
                IngestOutcome(
                    file_id=meta.file_id,
                    data_id=meta.data_id,
                    chain=chain,
                    off_table_fields=meta.off_table_fields(),
                ),
            )
        return self.ingest(
            meta, ingest_time=ingest_time, reuse_completed_chain=reuse_completed_chain
        )

    def _reject_on_compliance(
        self, meta: FileMeta, violation: ComplianceViolation, outcome: IngestOutcome
    ) -> IngestOutcome:
        """合规链路中断 → 走完整的五步异常闭环，而不是只返回一段 error 文本。

        这是本模块最容易漏的一条路径：文件连门禁都没走到就在第 ① / ② / ③ / ④ 步被拦下
        （脱敏标记缺失、合规云对外暴露、载荷不许过境……），如果只把异常转成
        ``IngestOutcome.error``，这条数据就**只活在调用方的返回值里**——隔离表没有它、
        没人被告警、也没有可复核的原始报文。而 [a8] 第六章说得很清楚：脱敏标记缺失是
        最高优先级的 P0，「命中拒绝规则的数据进入五步异常闭环」。

        所以这里补上拦截 → 隔离 → 告警：报文原样进 ``ods_quality_issue``，
        等人把脱敏补上之后按 ``recheck()`` 复验重入湖。
        """
        gate_outcome = GateOutcome(
            Decision.REJECT,
            [
                CheckResult(
                    CHAIN_COMPLIANCE_CHECK,
                    CheckStatus.FAIL,
                    (str(violation).replace("\n", " "), *violation.violations),
                )
            ],
            Severity.P0,
            subject=meta.file_id,
        )
        outcome.error = str(violation)
        outcome.gate = gate_outcome
        outcome.anomaly = self.closed_loop.handle(
            subject=meta.file_id,
            outcome=gate_outcome,
            channel=ChannelKind.OSS.label,
            target_table=self.channel.target_table,
            payload=meta.to_row(),
        )
        return outcome

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

    # ---- 五步异常闭环的后两步 ----

    def triage(
        self, record: AnomalyRecord, branch: TriageBranch | str, note: str = ""
    ) -> AnomalyRecord:
        """第 ④ 步 · 分流处置：A 自动修复 / B 人工修复 / C 弃置归档，三选一。"""
        return self.closed_loop.triage(record, branch, note)

    def recheck(self, record: AnomalyRecord, note: str = "") -> AnomalyRecord:
        """第 ⑤ 步 · 复验重入湖：拿隔离表里的原始报文**重跑全部门禁规则**。

        [a6] 第五章第 ⑤ 步：「重新执行全部门禁规则，通过则写入 ODS 并回填处理状态；
        不通过退回隔离，超 3 轮升级 P0」。这里把「重跑」与「重入湖」都接成真动作——
        重跑走的是同一个 ``OssComplianceGate``，重入湖走的是同一条 ``OssFileChannel``，
        复验用的门禁与首次入湖用的门禁是同一套，不存在「复验放水」。

        合规问题（脱敏标记缺失）不会在这里被放行：报文没补脱敏标记，重跑照样是 P0 拒绝；
        就算调用方伪造了结论，``AnomalyClosedLoop.recheck`` 也会抛。
        """

        def _rerun(row: Mapping[str, Any]) -> GateOutcome:
            return self.gate.check(FileMeta.from_kafka_message(row), row=row)

        def _reingest(row: Mapping[str, Any]) -> IngestReport:
            return self.channel.run([row], limit=1)

        return self.closed_loop.recheck(record, note=note, rerun=_rerun, reingest=_reingest)

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
        """隔离表 ``ods_quality_issue`` 的当前快照（拦截 → 隔离 的产物）。

        默认装配下这些行**已经**写进 sink 了（见构造函数的 ``isolating_closed_loop``）；
        本方法给的是同一批行的内存视图，便于直接断言与复核。
        """
        return self.closed_loop.issue_rows()

    def pending_recheck(self) -> Sequence[AnomalyRecord]:
        """还没闭环的被拦记录——「拦得住、找得回」里的找得回。"""
        return self.closed_loop.pending_recheck()
