"""合规最后一道闸：OSS 通道专属门禁 + 五步异常闭环。

来源：[a8] 第六章「合规最后一道闸：与质量门禁的衔接」。

原文：「元信息写入湖仓前，同样要过统一数据质量门禁。除通用规则外，本通道有
四项专属检查——其中两项是 P0 级」：

| 检查项 | 说明 | 处置 |
|---|---|---|
| 脱敏标记完整性 | 文件需携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险 | P0 拒绝入湖 |
| data_id 格式合法 | 全局数据 ID 格式与来源前缀合法性（血缘追溯起点） | P0 拒绝入湖 |
| 文件本体可解码 | 图像 / 点云文件完整性与可解码性校验 | P1 拒绝入湖 |
| 元信息与 OSS 路径一致 | file_path 合法且指向智驾云 OSS，checksum 可校验 | P1 拒绝入湖 |

「命中拒绝规则的数据进入五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）。
注意脱敏标记缺失是最高优先级的 P0——这不是数据质量问题，而是合规问题，
门禁在这里承担了合规的最后核验职责。」

**边界说明**：[a5] 第六 / 八章讲的是三通道共用的**通用门禁**（六维检查框架 +
ERROR 拒绝 / WARNING 带标放行三分支处置），它归 ``adas_lakehouse.quality`` 子系统。
本模块只实现 OSS 通道的四项专属检查，并通过 ``OssComplianceGate(generic_gate=...)``
留出把通用门禁挂进来的钩子——「通道可以分，门禁不能分」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .compliance import ComplianceMarks
from .constants import (
    ALERT_LEVELS,
    ANOMALY_CLOSED_LOOP_STEPS,
    OSS_CHANNEL_GATE_CHECKS,
    OSS_CHANNEL_P0_CHECKS,
    QUALITY_BRANCH_COUNT,
    QUALITY_DIMENSION_COUNT,
)
from .oss import (
    FileMeta,
    NullObjectStore,
    ObjectStore,
    probe_decodable,
    validate_data_id,
    validate_object_key,
    verify_checksum,
)

__all__ = [
    "Severity",
    "CheckStatus",
    "Decision",
    "GateCheck",
    "OSS_CHANNEL_CHECKS",
    "CheckResult",
    "GateOutcome",
    "decide",
    "merge_outcomes",
    "OssComplianceGate",
    "QUALITY_DIMENSIONS",
    "AnomalyStep",
    "AnomalyRecord",
    "AnomalyClosedLoop",
    "QUALITY_ISSUE_TABLE",
]

#: 异常隔离表。见 domains.QUALITY_GATE_PSEUDO_DOMAIN——它独立于 11 数据域之外，
#: 是入湖闸门的产物。表结构由 quality 子系统定义，本模块只负责构造行。
QUALITY_ISSUE_TABLE = "ods_quality_issue"


class Severity(str, Enum):
    """告警分级。[a5] 第八章：「异常数据进隔离表、按 P0~P3 分级告警」。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


assert tuple(s.value for s in Severity) == ALERT_LEVELS


class CheckStatus(str, Enum):
    """单项检查结论。

    SKIPPED 是本项目补的第三态（⚠️ 原文未明确，本项目设计）：原文只有通过 / 不通过，
    但对象存储探针不可用时（离线回放、权限受限）不应把「查不了」当成「查出问题」，
    否则门禁会变成误杀源。SKIPPED 走 WARNING 带标放行，并在隔离原因里留痕。
    """

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class Decision(str, Enum):
    """门禁三分支处置（[a5]：ERROR 拒绝、WARNING 带标放行）。"""

    ACCEPT = "accept"
    ACCEPT_WITH_WARNING = "accept_with_warning"
    REJECT = "reject"


assert len(Decision) == QUALITY_BRANCH_COUNT


@dataclass(frozen=True, slots=True)
class GateCheck:
    """一项门禁检查的定义。名称与说明取 [a8] 第六章表格原文。"""

    code: str
    name: str
    description: str
    severity: Severity
    #: 对应六维检查框架里的维度（[a5]：完整性 / 准确性 / 一致性 / 唯一性 / 有效性 / 及时性）
    dimension: str
    #: 是否属于合规问题而非数据质量问题（[a8]：脱敏标记缺失「不是数据质量问题，而是合规问题」）
    is_compliance: bool = False


#: [a8] 第六章：本通道四项专属检查，其中两项 P0
OSS_CHANNEL_CHECKS: tuple[GateCheck, ...] = (
    GateCheck(
        "redaction_marks_complete",
        "脱敏标记完整性",
        "文件需携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险",
        Severity.P0,
        "完整性",
        is_compliance=True,
    ),
    GateCheck(
        "data_id_format_valid",
        "data_id 格式合法",
        "全局数据 ID 格式与来源前缀合法性（血缘追溯起点）",
        Severity.P0,
        "有效性",
    ),
    GateCheck(
        "file_body_decodable",
        "文件本体可解码",
        "图像 / 点云文件完整性与可解码性校验",
        Severity.P1,
        "准确性",
    ),
    GateCheck(
        "meta_oss_path_consistent",
        "元信息与 OSS 路径一致",
        "file_path 合法且指向智驾云 OSS，checksum 可校验",
        Severity.P1,
        "一致性",
    ),
)

assert len(OSS_CHANNEL_CHECKS) == OSS_CHANNEL_GATE_CHECKS
assert sum(1 for c in OSS_CHANNEL_CHECKS if c.severity is Severity.P0) == OSS_CHANNEL_P0_CHECKS
#: 六维检查框架的完整维度表，专属检查各自映射到其中一维
QUALITY_DIMENSIONS: tuple[str, ...] = ("完整性", "准确性", "一致性", "唯一性", "有效性", "及时性")
assert len(QUALITY_DIMENSIONS) == QUALITY_DIMENSION_COUNT
assert {c.dimension for c in OSS_CHANNEL_CHECKS} <= set(QUALITY_DIMENSIONS)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """单项检查结果。"""

    check: GateCheck
    status: CheckStatus
    reasons: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return self.status is CheckStatus.FAIL

    def describe(self) -> str:
        base = f"[{self.check.severity.value}] {self.check.name}: {self.status.value}"
        if self.reasons:
            base += " — " + "；".join(self.reasons)
        return base


@dataclass(slots=True)
class GateOutcome:
    """一条记录的门禁结论。"""

    decision: Decision
    results: list[CheckResult] = field(default_factory=list)
    #: 拒绝时的最高告警级别
    severity: Severity | None = None
    checked_at: datetime = field(default_factory=datetime.now)
    subject: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision is not Decision.REJECT

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.failed]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.SKIPPED]

    def reason_text(self) -> str:
        parts = [r.describe() for r in self.results if r.status is not CheckStatus.PASS]
        return " | ".join(parts)

    def has_compliance_failure(self) -> bool:
        """是否命中合规问题（而非单纯的数据质量问题）。"""
        return any(r.check.is_compliance for r in self.failures)


def decide(results: Sequence[CheckResult], *, subject: str = "") -> GateOutcome:
    """把一组检查结果收敛成三分支处置（[a5]：ERROR 拒绝、WARNING 带标放行）。

    规则（三条通道共用同一套，「通道可以分，门禁不能分」）：
      · 任一 FAIL → REJECT，severity 取命中项里最高的（P0 > P1 > P2 > P3）；
      · 无 FAIL 但有 SKIPPED → ACCEPT_WITH_WARNING（带标放行，标级 P2）；
      · 全 PASS → ACCEPT。
    """
    failures = [r for r in results if r.failed]
    if failures:
        # P0 < P1 < P2 < P3 的字典序恰好就是告警优先级，min 即取最高级别
        severity = min((r.check.severity for r in failures), key=lambda s: s.value)
        return GateOutcome(Decision.REJECT, list(results), severity, subject=subject)
    if any(r.status is CheckStatus.SKIPPED for r in results):
        return GateOutcome(
            Decision.ACCEPT_WITH_WARNING, list(results), Severity.P2, subject=subject
        )
    return GateOutcome(Decision.ACCEPT, list(results), None, subject=subject)


def merge_outcomes(*outcomes: GateOutcome) -> GateOutcome:
    """合并多份门禁结论（通道专属规则 + 通用六维门禁）。

    合并后重新用 ``decide()`` 判一次——**不是**取最宽松的那个：只要有一份判 REJECT，
    合并结果就是 REJECT。这是「门禁不能分」在代码里的形状：挂进来的通用门禁只会
    让准入更严，不会把通道专属规则已经拦下的数据放行。
    """
    results: list[CheckResult] = []
    subject = ""
    for outcome in outcomes:
        results.extend(outcome.results)
        subject = subject or outcome.subject
    return decide(results, subject=subject)


class OssComplianceGate:
    """OSS 合规上传通道的质量门禁。

    Args:
        store: 对象存储探针，缺省 ``NullObjectStore``（不访问对象存储，P1 物理校验降级为
            带标放行）。生产环境传 ``S3CompatibleObjectStore()``。
        allowed_buckets: 智驾云 OSS bucket 白名单，缺省取 ``settings().minio.raw_bucket``。
        generic_gate: 通用六维门禁钩子（由 ``adas_lakehouse.quality`` 子系统提供）。
            签名 ``(row: Mapping[str, Any]) -> Sequence[CheckResult]``，返回的结果会与
            专属检查合并。不传则只跑本通道的四项专属检查。

    Example:
        >>> gate = OssComplianceGate()
        >>> outcome = gate.check(meta)          # doctest: +SKIP
        >>> outcome.decision                    # doctest: +SKIP
        <Decision.ACCEPT_WITH_WARNING: 'accept_with_warning'>
    """

    def __init__(
        self,
        store: ObjectStore | None = None,
        *,
        allowed_buckets: Sequence[str] | None = None,
        generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
    ) -> None:
        self.store = store if store is not None else NullObjectStore()
        self.allowed_buckets = tuple(allowed_buckets) if allowed_buckets else None
        self.generic_gate = generic_gate

    # ---- 四项专属检查 ----

    def _check_redaction_marks(self, marks: ComplianceMarks) -> CheckResult:
        """P0 · 脱敏标记完整性。合规问题，永远不带标放行。"""
        check = OSS_CHANNEL_CHECKS[0]
        problems = marks.validate()
        if problems:
            return CheckResult(check, CheckStatus.FAIL, tuple(problems))
        return CheckResult(check, CheckStatus.PASS)

    def _check_data_id(self, data_id: str) -> CheckResult:
        """P0 · data_id 格式合法（血缘追溯起点）。"""
        check = OSS_CHANNEL_CHECKS[1]
        problems = validate_data_id(data_id)
        if problems:
            return CheckResult(check, CheckStatus.FAIL, tuple(problems))
        return CheckResult(check, CheckStatus.PASS)

    def _check_decodable(self, meta: FileMeta) -> CheckResult:
        """P1 · 文件本体可解码。探针不可用时降级为 SKIPPED。

        探针结论回填到 ``meta.decodable_flag``，随元信息落契约列 ``decodable_flag``——
        契约对该列的说明是「上游解码探针的结论，随元信息落表」，不回填这一列就永远是
        NULL，下游 QG-OSS-006 的兜底判据也就失效了。探针不可用时保持 None（未知），
        不写成 False：查不了 ≠ 查出问题。
        """
        check = OSS_CHANNEL_CHECKS[2]
        probe = probe_decodable(meta, self.store)
        if not probe.known:
            return CheckResult(check, CheckStatus.SKIPPED, (probe.reason,))
        meta.decodable_flag = probe.decodable
        if not probe.decodable:
            return CheckResult(check, CheckStatus.FAIL, (probe.reason,))
        return CheckResult(check, CheckStatus.PASS)

    def _check_path_and_checksum(self, meta: FileMeta) -> CheckResult:
        """P1 · 元信息与 OSS 路径一致：file_path 指向智驾云 OSS 且 checksum 可校验。"""
        check = OSS_CHANNEL_CHECKS[3]
        problems = list(validate_object_key(meta.file_path, allowed_buckets=self.allowed_buckets))
        stat = self.store.head(meta.object_key)
        problems.extend(verify_checksum(meta.checksum_md5, stat=stat))
        if problems:
            return CheckResult(check, CheckStatus.FAIL, tuple(problems))
        if stat is None:
            return CheckResult(
                check,
                CheckStatus.SKIPPED,
                ("对象存储探针不可用，file_path 合法性已校验但未与对象实际比对",),
            )
        return CheckResult(check, CheckStatus.PASS)

    # ---- 汇总 ----

    def check(self, meta: FileMeta, *, row: Mapping[str, Any] | None = None) -> GateOutcome:
        """跑完四项专属检查（可选叠加通用门禁），给出三分支处置结论。

        处置规则：
          · 任一检查 FAIL → REJECT，severity 取命中项里最高的（P0 > P1 > P2 > P3）；
          · 无 FAIL 但有 SKIPPED → ACCEPT_WITH_WARNING（带标放行）；
          · 全 PASS → ACCEPT。
        """
        results = [
            self._check_redaction_marks(meta.marks),
            self._check_data_id(meta.data_id),
            self._check_decodable(meta),
            self._check_path_and_checksum(meta),
        ]
        if self.generic_gate is not None:
            results.extend(self.generic_gate(row if row is not None else meta.to_row()))

        return decide(results, subject=meta.file_id)


# --------------------------------------------------------------------------- 异常闭环


class AnomalyStep(str, Enum):
    """五步异常闭环（[a8] 第六章 / [a5] 第八章，逐字）。"""

    INTERCEPT = "拦截"
    ISOLATE = "隔离"
    ALERT = "告警"
    TRIAGE = "分流处置"
    RECHECK = "复验"


assert len(AnomalyStep) == ANOMALY_CLOSED_LOOP_STEPS


@dataclass(slots=True)
class AnomalyRecord:
    """一条被拦截数据的异常闭环记录。"""

    subject: str
    outcome: GateOutcome
    channel: str
    target_table: str
    steps: list[tuple[AnomalyStep, datetime, str]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def mark(self, step: AnomalyStep, note: str = "", moment: datetime | None = None) -> None:
        self.steps.append((step, moment or datetime.now(), note))

    @property
    def current_step(self) -> AnomalyStep | None:
        return self.steps[-1][0] if self.steps else None

    def to_issue_row(self) -> dict[str, Any]:
        """构造隔离表 ``ods_quality_issue`` 的一行。

        ⚠️ 原文未明确，本项目设计：原文只说「异常数据进隔离表」，未给隔离表 DDL。
        本行按「可重放 + 可追责 + 可复验」三要素构造；``ods_quality_issue`` 的实际
        列由 quality 子系统定义，落库前经 ``rows.project_to_table`` 投影，
        多余字段不会被写入。该表按 dt 分区（全湖 6 张分区表之一）。
        """
        now = datetime.now()
        return {
            "issue_id": f"{self.channel}_{self.subject}_{self.outcome.checked_at:%Y%m%d%H%M%S}",
            "dt": self.outcome.checked_at.strftime("%Y-%m-%d"),
            "subject_id": self.subject,
            "data_id": self.payload.get("data_id", ""),
            "source_channel": self.channel,
            "target_table": self.target_table,
            "severity": self.outcome.severity.value if self.outcome.severity else "",
            "is_compliance_issue": self.outcome.has_compliance_failure(),
            "check_codes": ",".join(r.check.code for r in self.outcome.failures),
            "issue_reason": self.outcome.reason_text(),
            "closed_loop_step": self.current_step.value if self.current_step else "",
            "raw_payload": repr(self.payload),
            "intercepted_at": self.outcome.checked_at,
            "updated_at": now,
        }


class AnomalyClosedLoop:
    """五步异常闭环执行器：拦截 → 隔离 → 告警 → 分流处置 → 复验。

    Args:
        isolate: 隔离动作，签名 ``(table, row) -> None``。缺省只在内存里留存，
            生产环境接 ``sinks.OdsSink`` 写 ``ods_quality_issue``。
        alert: 告警动作，签名 ``(severity, message) -> None``。缺省 no-op。

    Note:
        「分流处置」与「复验」需要人/工单参与，本类只负责把状态推进到位并留痕，
        不替业务决定怎么修——⚠️ 原文未明确，本项目设计：原文没给分流规则明细。
        唯一的硬规则来自 [a8]：命中合规问题（脱敏标记缺失）的数据不允许复验放行，
        必须退回合规云重做双脱敏。
    """

    def __init__(
        self,
        isolate: Callable[[str, Mapping[str, Any]], Any] | None = None,
        alert: Callable[[Severity, str], None] | None = None,
    ) -> None:
        self._isolate = isolate
        self._alert = alert
        self.records: list[AnomalyRecord] = []

    def handle(
        self,
        *,
        subject: str,
        outcome: GateOutcome,
        channel: str,
        target_table: str,
        payload: Mapping[str, Any] | None = None,
    ) -> AnomalyRecord:
        """把一条被拒记录推过前三步（拦截 → 隔离 → 告警），返回闭环记录。

        后两步（分流处置 / 复验）由 ``triage()`` 与 ``recheck()`` 在人工或工单
        回调时推进。
        """
        record = AnomalyRecord(subject, outcome, channel, target_table, payload=dict(payload or {}))

        record.mark(AnomalyStep.INTERCEPT, outcome.reason_text())

        row = record.to_issue_row()
        if self._isolate is not None:
            self._isolate(QUALITY_ISSUE_TABLE, row)
        record.mark(AnomalyStep.ISOLATE, f"写入 {QUALITY_ISSUE_TABLE}")

        severity = outcome.severity or Severity.P2
        message = (
            f"[{severity.value}] {channel} 通道拦截 {subject}"
            f"{'（合规问题）' if outcome.has_compliance_failure() else ''}：{outcome.reason_text()}"
        )
        if self._alert is not None:
            self._alert(severity, message)
        record.mark(AnomalyStep.ALERT, message)

        self.records.append(record)
        return record

    def triage(self, record: AnomalyRecord, disposition: str) -> AnomalyRecord:
        """第四步 · 分流处置：记录处置结论（重传 / 退回合规云 / 豁免 / 丢弃）。"""
        record.mark(AnomalyStep.TRIAGE, disposition)
        return record

    def recheck(self, record: AnomalyRecord, passed: bool, note: str = "") -> AnomalyRecord:
        """第五步 · 复验。

        Raises:
            ValueError: 试图对合规问题（脱敏标记缺失）直接复验放行——
                [a8]：这类问题必须退回合规云重做双脱敏，门禁不接受「放行」结论。
        """
        if passed and record.outcome.has_compliance_failure():
            raise ValueError(
                "合规问题（脱敏标记缺失）不得复验放行：必须退回合规云补做双脱敏后重新走五步链路"
            )
        record.mark(AnomalyStep.RECHECK, f"{'通过' if passed else '未通过'} {note}".strip())
        return record
