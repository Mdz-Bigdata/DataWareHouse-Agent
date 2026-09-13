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

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from .compliance import ComplianceMarks
from .constants import (
    ALERT_LEVELS,
    ANOMALY_CLOSED_LOOP_STEPS,
    MAX_RECHECK_ROUNDS,
    OSS_CHANNEL_GATE_CHECKS,
    OSS_CHANNEL_P0_CHECKS,
    QUALITY_BRANCH_COUNT,
    QUALITY_DIMENSION_COUNT,
    TRIAGE_BRANCH_COUNT,
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
    "DIMENSION_KEYS",
    "ISSUE_CHANNEL_CODES",
    "AnomalyStep",
    "TriageBranch",
    "IssueStatus",
    "AlertNotice",
    "AnomalyRecord",
    "AnomalyClosedLoop",
    "dump_payload",
    "load_payload",
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

#: 六维的中文名 → 共享契约 ``ods_quality_issue.dimension`` 的取值口径
#: （「completeness/accuracy/consistency/uniqueness/validity/timeliness」）。
#: 隔离表由本子系统与 ``adas_lakehouse.quality`` **两个写入方**共同写，列里躺的必须是
#: 同一套取值，否则同一张表里会出现两种方言，下游按维度分组统计立刻裂成两半。
DIMENSION_KEYS: dict[str, str] = {
    "完整性": "completeness",
    "准确性": "accuracy",
    "一致性": "consistency",
    "唯一性": "uniqueness",
    "有效性": "validity",
    "及时性": "timeliness",
}
assert set(DIMENSION_KEYS) == set(QUALITY_DIMENSIONS)

#: 通道标签（[a8] 第一章表格第一列）→ 共享契约 ``ods_quality_issue.source_channel`` 的取值。
#: 取值随 ``quality.severity.Channel``（mysql_cdc / kafka / oss_file），理由同 ``DIMENSION_KEYS``：
#: 两个写入方写同一列，就必须用同一套字面量。``channels.ChannelKind.issue_code`` 与
#: ``quality_bridge.CHANNEL_MAP`` 都从这里取，不再各写一份。
ISSUE_CHANNEL_CODES: dict[str, str] = {
    "Flink CDC": "mysql_cdc",
    "Kafka": "kafka",
    "OSS 合规上传": "oss_file",
}


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
        forbidden_buckets: 明令禁止的 bucket——合规云对象存储。[a8] 第四章：合规云
            「不对外暴露」，能离开的只有脱敏脱密后的副本与元信息；所以一条指向合规云
            bucket 的 ``file_path`` 不是「bucket 写错了」，是**跨域分发边界被越过**，
            拒绝理由必须说清楚。``ComplianceIngestPipeline`` 会把
            ``ComplianceCloudTopology.compliance_cloud_bucket`` 传到这里。
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
        forbidden_buckets: Sequence[str] | None = None,
        generic_gate: Callable[[Mapping[str, Any]], Sequence[CheckResult]] | None = None,
    ) -> None:
        self.store = store if store is not None else NullObjectStore()
        self.allowed_buckets = tuple(allowed_buckets) if allowed_buckets else None
        self.forbidden_buckets = tuple(b for b in (forbidden_buckets or ()) if b)
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
        problems = list(
            validate_object_key(
                meta.file_path,
                allowed_buckets=self.allowed_buckets,
                forbidden_buckets=self.forbidden_buckets,
            )
        )
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


class TriageBranch(str, Enum):
    """第 ④ 步「分流处置」的三个分支（[a6] 第五章 ④，逐字）。

    A 自动修复（重传 / 幂等重放 / 断点续传）、B 人工修复（源端补数，工单跟踪）、
    C 弃置归档（无法修复，标记原因后归档保留审计）。

    取值与共享契约 ``ods_quality_issue.repair_action``（「A 自动修复 / B 人工修复 /
    C 弃置归档」）以及 ``quality.RepairAction`` 同口径——同一张隔离表两个写入方，
    列里不能出现两套方言。
    """

    AUTO = "auto_repair"
    MANUAL = "manual_repair"
    DISCARD = "discard_archive"

    @property
    def label(self) -> str:
        return {
            TriageBranch.AUTO: "A 自动修复（重传 / 幂等重放 / 断点续传）",
            TriageBranch.MANUAL: "B 人工修复（源端补数，工单跟踪）",
            TriageBranch.DISCARD: "C 弃置归档（无法修复，标记原因后归档保留审计）",
        }[self]


assert len(TriageBranch) == TRIAGE_BRANCH_COUNT


class IssueStatus(str, Enum):
    """隔离记录在五步闭环里的状态。

    取值与共享契约 ``ods_quality_issue.issue_status``（「isolated/alerted/dispatched/
    repaired/rechecking/reingested/discarded」）逐字对齐。
    """

    ISOLATED = "isolated"
    ALERTED = "alerted"
    DISPATCHED = "dispatched"
    RECHECKING = "rechecking"
    REINGESTED = "reingested"
    DISCARDED = "discarded"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return repr(value)


def dump_payload(payload: Mapping[str, Any]) -> str:
    """把原始报文序列化成**可重放**的 JSON 文本。

    [a6] 第三章：「被拒绝的数据连同命中规则一起落表，而不是打日志了事。
    **原始数据不丢失**是整套门禁可重放、可审计的根基。」——所以这里不能用 ``repr()``：
    报文里的 ``datetime``（``redaction_vehicle_time`` / ``distributed_at``）经 ``repr``
    会变成 ``datetime.datetime(2026, 3, 1, ...)``，既不是 JSON 也过不了
    ``ast.literal_eval``，隔离表里那一行就成了只能用眼睛看的字符串，复验重放无从谈起。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def load_payload(raw: str) -> dict[str, Any]:
    """``dump_payload`` 的逆操作：从隔离表的 ``raw_payload`` 还原一行，供复验重放。

    Raises:
        ValueError: 报文不是合法 JSON —— 这一条不具备重放条件（``replayable=False``）。
    """
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"raw_payload 不是合法 JSON，无法重放: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"raw_payload 不是一行记录（得到 {type(loaded).__name__}）")
    return loaded


@dataclass(slots=True)
class AnomalyRecord:
    """一条被拦截数据的异常闭环记录 = 隔离表 ``ods_quality_issue`` 的一行。

    三组字段对应 [a6] 第五章对隔离表的要求「拦得住、找得回、修得好」：

      · 找得回 —— ``subject`` / ``payload`` / ``payload_hash`` / ``replayable``；
      · 说得清 —— ``outcome``（命中规则 + 维度 + 等级）；
      · 修得好 —— ``status`` / ``branch`` / ``recheck_rounds`` / ``escalated``。
    """

    subject: str
    outcome: GateOutcome
    channel: str
    target_table: str
    steps: list[tuple[AnomalyStep, datetime, str]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    status: IssueStatus = IssueStatus.ISOLATED
    #: ④ 分流处置选的分支；None 表示尚未分流
    branch: TriageBranch | None = None
    #: ⑤ 复验轮次。[a6]：「不通过退回隔离，超 3 轮升级 P0」
    recheck_rounds: int = 0
    #: 是否已因复验超轮次升级到 P0
    escalated: bool = False
    resolved_at: datetime | None = None
    reingested_at: datetime | None = None
    discard_reason: str = ""

    def mark(self, step: AnomalyStep, note: str = "", moment: datetime | None = None) -> None:
        self.steps.append((step, moment or datetime.now(), note))

    @property
    def current_step(self) -> AnomalyStep | None:
        return self.steps[-1][0] if self.steps else None

    @property
    def issue_id(self) -> str:
        """隔离记录 ID。同一条被拦数据重复拦截时 ID 稳定——隔离动作要幂等。"""
        return f"{self.channel_code}_{self.subject}_{self.outcome.checked_at:%Y%m%d%H%M%S}"

    @property
    def channel_code(self) -> str:
        """通道标签 → 契约取值（mysql_cdc / kafka / oss_file）。未登记的标签原样透传。"""
        return ISSUE_CHANNEL_CODES.get(self.channel, self.channel)

    @property
    def severity(self) -> Severity:
        """当前告警级别。升级后恒为 P0（[a6]：复验超 3 轮升级 P0）。"""
        if self.escalated:
            return Severity.P0
        return self.outcome.severity or Severity.P2

    @property
    def raw_payload(self) -> str:
        return dump_payload(self.payload)

    @property
    def replayable(self) -> bool:
        """是否具备重放条件：原始报文完整可还原 + 目标表已知。"""
        if not self.payload or not self.target_table:
            return False
        try:
            load_payload(self.raw_payload)
        except ValueError:
            return False
        return True

    def replay_payload(self) -> dict[str, Any]:
        """取回可重新过门禁的那一行（⑤ 复验重入湖的输入）。"""
        return load_payload(self.raw_payload)

    def history_json(self) -> str:
        """处理轨迹（JSON 数组）：五步闭环每次状态推进追加一条，可追责。"""
        return json.dumps(
            [
                {"step": s.value, "at": t.isoformat(sep=" ", timespec="milliseconds"), "note": n}
                for s, t, n in self.steps
            ],
            ensure_ascii=False,
        )

    def to_issue_row(self) -> dict[str, Any]:
        """构造隔离表 ``ods_quality_issue`` 的一行，**按共享契约的列名**。

        隔离表的列由 ``catalog/tables/_quality.py`` 定义（契约只读），且有两套并存的
        同义列名——旧列（``source_record_key`` / ``rule_id`` / ``issue_detail`` /
        ``isolate_time`` …）与 quality 子系统实际读写的新列（``record_key`` /
        ``rule_ids`` / ``detail`` / ``detected_at`` …）。本方法**两套都填**：
        契约把它们当同义列并存，下游读哪一套都不该取到 NULL。

        为什么必须按契约列名：落库前经 ``rows.project_to_table`` 投影到注册列，
        自造的列名（曾经的 ``subject_id`` / ``check_codes`` / ``issue_reason`` /
        ``closed_loop_step``）会被整列丢掉——隔离表里只剩一个 ID 和一段报文，
        「连同命中规则一起落表」就落空了。

        ⚠️ 契约缺列：``is_compliance_issue``（[a8]「这不是数据质量问题，而是合规问题」）
        在 ``ods_quality_issue`` 里没有对应列，本行只能把它藏进 ``rule_ids``
        （命中 ``redaction_marks_complete`` 即合规问题）与 ``detail`` 的文字里。
        """
        failures = self.outcome.failures
        dimensions = [DIMENSION_KEYS.get(r.check.dimension, r.check.dimension) for r in failures]
        rule_ids = ",".join(r.check.code for r in failures)
        reason = self.outcome.reason_text()
        level = self.severity.value
        # 契约 severity 列是「检查器严重度：ERROR→拒绝入湖 / WARNING→带标记放行」，
        # P0~P3 是 issue_level 列。两列语义不同，不能混着写。
        checker_severity = "ERROR" if self.outcome.decision is Decision.REJECT else "WARNING"
        compliance_note = (
            "（合规问题，非数据质量问题）" if self.outcome.has_compliance_failure() else ""
        )
        row: dict[str, Any] = {
            "issue_id": self.issue_id,
            "dt": self.outcome.checked_at.strftime("%Y-%m-%d"),
            "data_id": self.payload.get("data_id", ""),
            "project_code": self.payload.get("project_code", ""),
            "vehicle_code": self.payload.get("vehicle_code", ""),
            "source_channel": self.channel_code,
            # 旧列 / 新列同义对照：target_table≈source_table、source_record_key≈record_key …
            "target_table": self.target_table,
            "source_table": self.target_table,
            "source_record_key": self.subject,
            "record_key": self.subject,
            "rule_id": rule_ids,
            "rule_ids": rule_ids,
            "rule_dimension": ",".join(dict.fromkeys(dimensions)),
            "dimension": ",".join(dict.fromkeys(dimensions)),
            "severity": checker_severity,
            "issue_level": level,
            "issue_detail": reason + compliance_note,
            "detail": reason + compliance_note,
            "message": "；".join(r.check.name for r in failures),
            "hits_json": json.dumps(
                [
                    {
                        "rule_id": r.check.code,
                        "name": r.check.name,
                        "issue_level": r.check.severity.value,
                        "dimension": DIMENSION_KEYS.get(r.check.dimension, r.check.dimension),
                        "is_compliance": r.check.is_compliance,
                        "reasons": list(r.reasons),
                    }
                    for r in failures
                ],
                ensure_ascii=False,
            ),
            "raw_payload": self.raw_payload,
            "payload_hash": hashlib.sha256(self.raw_payload.encode("utf-8")).hexdigest()[:16],
            "replayable": self.replayable,
            "isolate_time": self.outcome.checked_at,
            "detected_at": self.outcome.checked_at,
            "handle_status": self.status.value,
            "issue_status": self.status.value,
            "handle_strategy": self.branch.value if self.branch else "",
            "repair_action": self.branch.value if self.branch else "",
            "recheck_round": self.recheck_rounds,
            "recheck_count": self.recheck_rounds,
            "escalated": self.escalated,
            "resolved_time": self.resolved_at,
            "resolved_at": self.resolved_at,
            "reingested_at": self.reingested_at,
            "discard_reason": self.discard_reason,
            "history": self.history_json(),
        }
        return row


@dataclass(frozen=True, slots=True)
class AlertNotice:
    """一条已发出的分级告警。留在内存里，供复核与断言——告警不能只是一次 no-op 调用。"""

    severity: Severity
    message: str
    subject: str
    channel: str
    is_compliance: bool
    at: datetime


class AnomalyClosedLoop:
    """五步异常闭环执行器：拦截 → 隔离 → 告警 → 分流处置 → 复验。

    Args:
        isolate: 隔离动作，签名 ``(table, row) -> None``。生产环境接
            ``sinks.OdsSink`` 写 ``ods_quality_issue``（``pipeline`` 与
            ``channels.build_default_channels`` 已默认这样接线）。不传则只在内存留存，
            ``issue_rows()`` 仍能把待落库的行交出来——**任何路径下都不会静默丢弃**。
        alert: 告警动作，签名 ``(severity, message) -> None``。不论传不传，每条告警都会
            记进 ``self.alerts``：[a6] 第五章第 ③ 步要求分级告警通知「数据 owner +
            平台值班」，通知渠道（电话 / 钉钉 / 工单 / 日报 / 周报）由 quality 子系统的
            ``AlertRouter`` 负责，本类只保证「告警发生过、可复核」。

    Note:
        [a6] 第五章第 ④ 步的分流三分支与第 ⑤ 步的复验规则在这里是真执行的：
        ``triage()`` 只接受三分支枚举，``recheck()`` 重跑门禁、计轮次、超
        ``MAX_RECHECK_ROUNDS``（3 轮）升级 P0。唯一额外的硬规则来自 [a8]：命中合规问题
        （脱敏标记缺失）的数据不允许复验放行，必须退回合规云重做双脱敏。
    """

    def __init__(
        self,
        isolate: Callable[[str, Mapping[str, Any]], Any] | None = None,
        alert: Callable[[Severity, str], None] | None = None,
    ) -> None:
        self._isolate = isolate
        self._alert = alert
        self.records: list[AnomalyRecord] = []
        #: 已发出的分级告警（含升级告警），按时间顺序
        self.alerts: list[AlertNotice] = []

    # ---- ①②③ 拦截 → 隔离 → 告警 ----

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

        self._write_issue(record)
        record.status = IssueStatus.ISOLATED
        record.mark(AnomalyStep.ISOLATE, f"写入 {QUALITY_ISSUE_TABLE}")

        self._raise_alert(record, f"{channel} 通道拦截 {subject}：{outcome.reason_text()}")
        record.status = IssueStatus.ALERTED
        record.mark(AnomalyStep.ALERT, self.alerts[-1].message)

        self.records.append(record)
        self._write_issue(record)  # 状态推进后回写，隔离表里的 issue_status 才是最新的
        return record

    def _write_issue(self, record: AnomalyRecord) -> None:
        """把当前状态落隔离表。同一条记录的 ``issue_id`` 稳定，重复写是 upsert 不是新增。"""
        if self._isolate is not None:
            self._isolate(QUALITY_ISSUE_TABLE, record.to_issue_row())

    def alert_only(
        self, severity: Severity, message: str, *, channel: str = "", subject: str = ""
    ) -> AlertNotice:
        """发一条不带隔离记录的告警（监控类指标越线，数据本身没被拦）。

        [a6] 第二章：及时性、唯一性这两维的处置是「监控告警（不拦截）」「自动去重，
        重复率超限告警」——数据照常入湖，但告警必须发出去，而且要能被复核。
        """
        notice = AlertNotice(
            severity=severity,
            message=f"[{severity.value}] {message}",
            subject=subject,
            channel=channel,
            is_compliance=False,
            at=datetime.now(),
        )
        self.alerts.append(notice)
        if self._alert is not None:
            self._alert(severity, notice.message)
        return notice

    def _raise_alert(self, record: AnomalyRecord, message: str) -> AlertNotice:
        severity = record.severity
        compliance = record.outcome.has_compliance_failure()
        text = f"[{severity.value}] {message}{'（合规问题）' if compliance else ''}"
        notice = AlertNotice(
            severity=severity,
            message=text,
            subject=record.subject,
            channel=record.channel,
            is_compliance=compliance,
            at=datetime.now(),
        )
        self.alerts.append(notice)
        if self._alert is not None:
            self._alert(severity, text)
        return notice

    # ---- ④ 分流处置 ----

    def triage(
        self,
        record: AnomalyRecord,
        branch: TriageBranch | str,
        note: str = "",
    ) -> AnomalyRecord:
        """第四步 · 分流处置：在 [a6] 的三分支里选一条，并落回隔离表。

        Args:
            branch: ``TriageBranch`` 三选一（也接受其 value 字符串）。
            note: 处置说明；``DISCARD`` 分支下同时写进 ``discard_reason``——
                [a6]：「C 弃置归档（无法修复，**标记原因后**归档保留审计）」。

        Raises:
            ValueError: 分支不在三分支内；或试图把合规问题直接弃置归档
                （[a8]：脱敏标记缺失必须退回合规云重做双脱敏，不能一弃了之）。
        """
        chosen = branch if isinstance(branch, TriageBranch) else TriageBranch(branch)
        if chosen is TriageBranch.DISCARD:
            if record.outcome.has_compliance_failure():
                raise ValueError(
                    "合规问题（脱敏标记缺失）不得弃置归档：必须退回合规云补做双脱敏后重走五步链路"
                )
            if not note:
                raise ValueError("C 弃置归档必须标记原因后归档保留审计，note 不能为空")
            record.discard_reason = note
            record.status = IssueStatus.DISCARDED
            record.resolved_at = datetime.now()
        else:
            record.status = IssueStatus.DISPATCHED
        record.branch = chosen
        record.mark(AnomalyStep.TRIAGE, f"{chosen.label} {note}".strip())
        self._write_issue(record)
        return record

    # ---- ⑤ 复验重入湖 ----

    def recheck(
        self,
        record: AnomalyRecord,
        passed: bool | None = None,
        note: str = "",
        *,
        rerun: Callable[[Mapping[str, Any]], GateOutcome] | None = None,
        reingest: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> AnomalyRecord:
        """第五步 · 复验重入湖。

        [a6] 第五章第 ⑤ 步逐字：「**重新执行全部门禁规则**，通过则写入 ODS 并回填处理
        状态；不通过退回隔离，**超 3 轮升级 P0**」。

        Args:
            record: 被拦记录。
            passed: 复验结论。给了 ``rerun`` 时不用传——结论由重跑门禁得出，
                避免出现「门禁说拒、调用方说通过」这种说了不算的复验。
            note: 备注。
            rerun: 重跑全部门禁规则的回调，签名 ``(row) -> GateOutcome``。
                入参是从隔离表 ``raw_payload`` 还原出来的原始报文。
            reingest: 复验通过后的写 ODS 回调，签名 ``(row) -> Any``。

        Raises:
            ValueError: 既没给 ``rerun`` 也没给 ``passed``；报文不可重放；
                或试图让合规问题（脱敏标记缺失）复验放行——[a8]：这类问题必须退回
                合规云重做双脱敏，门禁不接受「放行」结论。
        """
        record.status = IssueStatus.RECHECKING
        record.recheck_rounds += 1

        detail = note
        if rerun is not None:
            row = record.replay_payload()  # 不可重放会抛 ValueError，不静默当成失败
            outcome = rerun(row)
            passed = outcome.decision is not Decision.REJECT
            detail = (note + " " + outcome.reason_text()).strip() if not passed else note
            if not passed:
                # 复验重跑的结论覆盖旧结论：第二轮命中的规则可能与第一轮不同
                record.outcome = outcome
        elif passed is None:
            raise ValueError("复验必须给出结论：要么传 rerun 回调重跑门禁，要么显式传 passed")

        if passed and record.outcome.has_compliance_failure():
            raise ValueError(
                "合规问题（脱敏标记缺失）不得复验放行：必须退回合规云补做双脱敏后重新走五步链路"
            )

        if passed:
            if reingest is not None:
                reingest(record.replay_payload())
            now = datetime.now()
            record.status = IssueStatus.REINGESTED
            record.reingested_at = now
            record.resolved_at = now
            record.mark(
                AnomalyStep.RECHECK, f"第 {record.recheck_rounds} 轮通过，重入湖 {detail}".strip()
            )
            self._write_issue(record)
            return record

        # 不通过 → 退回隔离
        record.status = IssueStatus.ISOLATED
        record.mark(
            AnomalyStep.RECHECK,
            f"第 {record.recheck_rounds} 轮未通过，退回隔离 {detail}".strip(),
        )
        if record.recheck_rounds > MAX_RECHECK_ROUNDS and not record.escalated:
            record.escalated = True
            self._raise_alert(
                record,
                f"{record.subject} 复验已超 {MAX_RECHECK_ROUNDS} 轮"
                f"（当前第 {record.recheck_rounds} 轮），升级 P0",
            )
            record.mark(AnomalyStep.ALERT, self.alerts[-1].message)
        self._write_issue(record)
        return record

    # ---- 观测 ----

    def issue_rows(self) -> list[dict[str, Any]]:
        """全部隔离记录的当前快照，可直接写 ``ods_quality_issue``。"""
        return [r.to_issue_row() for r in self.records]

    def pending_recheck(self) -> list[AnomalyRecord]:
        """还没闭环的记录（未重入湖、未弃置归档）——「找得回」的落点。"""
        return [
            r
            for r in self.records
            if r.status not in (IssueStatus.REINGESTED, IssueStatus.DISCARDED)
        ]
