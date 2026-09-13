"""合规入湖链路：五步链路 + 双脱敏机制 + 合规云架构约束。

来源：[a8]《采集数据的合规入湖链路》第二 / 三 / 四章。

一句话骨架：采集数据从车端硬盘到数据湖仓走五步，前三步是「合规处理段」（车端 →
合规室 → 合规云），后两步是「智驾云内的分发与入湖」。关键分界线是——
**进入智驾云的只有「合规数据副本 + 文件元信息」**，原始硬盘数据的完整生命周期
止步于合规云。

本模块把这三件事做成可执行的约束而非文档：
  · ``ComplianceChain``     五步状态机，顺序不能乱、步骤不能跳；
  · ``ComplianceMarks``     双脱敏标记，缺一即 P0 合规风险（见 gate.py）；
  · ``ComplianceCloudTopology`` 合规云三条架构约束的校验器。

[a8] 结语：「合规靠架构边界，不靠流程承诺」——所以这里的违规一律抛
``ComplianceViolation``，没有「记个日志放行」的分支。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any

from .constants import (
    ADAS_CLOUD_SEGMENT_STEPS,
    COMPLIANCE_CHAIN_STEPS,
    COMPLIANCE_CLOUD_CONSTRAINTS,
    COMPLIANCE_PROCESSING_SEGMENT_STEPS,
    REDACTION_STAGE_COUNT,
)
from .errors import ComplianceViolation

__all__ = [
    "ComplianceSite",
    "ComplianceStep",
    "StepDefinition",
    "STEP_DEFINITIONS",
    "RedactionStage",
    "RedactionDefinition",
    "REDACTION_DEFINITIONS",
    "RedactionMark",
    "ComplianceMarks",
    "CONTRACT_REDACTION_FLAGS",
    "ComplianceCloudTopology",
    "CrossBoundaryPayload",
    "assert_may_cross_boundary",
    "DISTRIBUTION_PAYLOADS",
    "ComplianceChain",
]


class ComplianceSite(str, Enum):
    """五步链路的发生地。[a8] 第二章表格第二列，逐字。"""

    VEHICLE = "车端"
    COMPLIANCE_ROOM = "合规室"
    COMPLIANCE_CLOUD = "合规云"
    ADAS_CLOUD = "智驾云"
    LAKEHOUSE = "湖仓"


class ComplianceStep(IntEnum):
    """五步合规链路。序号即执行顺序，不可乱序、不可跳步。

    ① 车端脱敏 → ② 合规室上传 → ③ 合规脱密 → ④ 合规数据分发 → ⑤ 实时入湖
    """

    VEHICLE_REDACTION = 1
    COMPLIANCE_ROOM_UPLOAD = 2
    COMPLIANCE_CLOUD_DESENSITIZATION = 3
    COMPLIANT_DATA_DISTRIBUTION = 4
    REALTIME_INGEST = 5

    @property
    def label(self) -> str:
        return STEP_DEFINITIONS[self].label

    @property
    def site(self) -> ComplianceSite:
        return STEP_DEFINITIONS[self].site

    @property
    def segment(self) -> str:
        """所属链路段：前三步「合规处理段」，后两步「智驾云内的分发与入湖」。"""
        return (
            "合规处理段"
            if self.value <= COMPLIANCE_PROCESSING_SEGMENT_STEPS
            else "智驾云内的分发与入湖"
        )


@dataclass(frozen=True, slots=True)
class StepDefinition:
    """一步的定义。``key_action`` 为 [a8] 第二章表格「关键动作」列原文。"""

    step: ComplianceStep
    label: str
    site: ComplianceSite
    key_action: str


#: [a8] 第二章「五步合规链路：从车端硬盘到湖仓」表格，逐字落库
STEP_DEFINITIONS: dict[ComplianceStep, StepDefinition] = {
    ComplianceStep.VEHICLE_REDACTION: StepDefinition(
        ComplianceStep.VEHICLE_REDACTION,
        "车端脱敏",
        ComplianceSite.VEHICLE,
        "采集时实时执行个性脱敏（人脸 / 车牌）+ 地信脱敏，数据落盘；"
        "落盘完成后由专门合规人员提取硬盘",
    ),
    ComplianceStep.COMPLIANCE_ROOM_UPLOAD: StepDefinition(
        ComplianceStep.COMPLIANCE_ROOM_UPLOAD,
        "合规室上传",
        ComplianceSite.COMPLIANCE_ROOM,
        "合规人员将硬盘带入合规室，上传至合规云对象存储（不对外暴露）",
    ),
    ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION: StepDefinition(
        ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION,
        "合规脱密",
        ComplianceSite.COMPLIANCE_CLOUD,
        "合规公司进一步脱密：删除敏感 POI（军事设施等）、模糊桥梁限高等具体数值",
    ),
    ComplianceStep.COMPLIANT_DATA_DISTRIBUTION: StepDefinition(
        ComplianceStep.COMPLIANT_DATA_DISTRIBUTION,
        "合规数据分发",
        ComplianceSite.ADAS_CLOUD,
        "合规数据副本复制至智驾云 OSS，同时向业务方 Kafka 发送文件元信息（同一 VPC 流转）",
    ),
    ComplianceStep.REALTIME_INGEST: StepDefinition(
        ComplianceStep.REALTIME_INGEST,
        "实时入湖",
        ComplianceSite.LAKEHOUSE,
        "业务方实时消费 Kafka，元信息经质量门禁写入 ods_data_file_meta，进入后续加工",
    ),
}

assert len(STEP_DEFINITIONS) == COMPLIANCE_CHAIN_STEPS
assert (
    sum(1 for s in ComplianceStep if s.segment == "智驾云内的分发与入湖")
    == ADAS_CLOUD_SEGMENT_STEPS
)


# --------------------------------------------------------------------------- 双脱敏


class RedactionStage(str, Enum):
    """两次脱敏。[a8] 第三章：「两段脱敏缺一不可，理由互为补充」。"""

    VEHICLE_SIMPLE = "vehicle_simple"
    CLOUD_COMPLEX = "cloud_complex"


@dataclass(frozen=True, slots=True)
class RedactionDefinition:
    """一段脱敏的定义，字段取 [a8] 第三章表格三列原文。"""

    stage: RedactionStage
    label: str
    content: str
    solves: str
    #: 只做这一段会留下的缺口（[a8] 第三章「理由互为补充」两条）
    gap_if_alone: str


REDACTION_DEFINITIONS: dict[RedactionStage, RedactionDefinition] = {
    RedactionStage.VEHICLE_SIMPLE: RedactionDefinition(
        RedactionStage.VEHICLE_SIMPLE,
        "车端简单脱敏",
        "采集时实时执行：人脸 / 车牌等个性脱敏 + 地信脱敏",
        "数据离车即合规——硬盘离开车的那一刻就不含个人信息",
        "只做车端脱敏：敏感地理信息（军事设施、桥梁限高数值）的识别与处理"
        "需要云端的大模型与地图知识库，车端实时算力做不到",
    ),
    RedactionStage.CLOUD_COMPLEX: RedactionDefinition(
        RedactionStage.CLOUD_COMPLEX,
        "合规云复杂脱敏",
        "合规公司进一步脱密：删除敏感 POI、模糊桥梁限高等具体数值",
        "车端算力与资质做不了的云端复杂脱敏",
        "只做云端脱敏：人脸车牌在数据离车后才处理，意味着原始数据在运输、存储环节"
        "全程「裸奔」，任何一环失控都是合规事故",
    ),
}

assert len(REDACTION_DEFINITIONS) == REDACTION_STAGE_COUNT


@dataclass(frozen=True, slots=True)
class RedactionMark:
    """一段脱敏的执行标记，随文件元信息流转到湖仓。

    ⚠️ 原文未明确，本项目设计：原文只说文件需「携带『车端脱敏 + 合规云脱密』双合规标记」，
    没有给标记的字段结构。本项目取「谁在何时按哪些规则做了这段脱敏」四要素——
    operator / applied_at / rules 三项都是合规审计追责时的最小必要信息。
    """

    stage: RedactionStage
    applied: bool
    #: 执行方：车端为采集软件版本，合规云为具备资质的合规公司
    operator: str = ""
    applied_at: datetime | None = None
    #: 实际生效的脱敏规则标识，例如 ("face_blur", "plate_blur", "geo_offset")
    rules: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        """标记自洽性校验：声称做过就必须给出执行方与时间。"""
        problems: list[str] = []
        definition = REDACTION_DEFINITIONS[self.stage]
        if not self.applied:
            problems.append(f"{definition.label}未执行：{definition.gap_if_alone}")
            return problems
        if not self.operator:
            problems.append(f"{definition.label}标记缺少执行方 operator")
        if self.applied_at is None:
            problems.append(f"{definition.label}标记缺少执行时间 applied_at")
        if not self.rules:
            problems.append(f"{definition.label}标记缺少生效规则列表 rules")
        return problems


#: 双合规标记在**共享契约**里的列名（``catalog/tables/_collect.py`` 的
#: ``ods_data_file_meta``）。契约把这两列写成 QG-OSS-001「脱敏标记合规」的判据：
#: 「必须携带『车端脱敏 + 合规云脱密』双合规标记，缺失即合规风险」。
#: 入湖侧的扁平字典必须同时带上这两个名字，否则投影到湖表时双脱敏标记会被静默丢掉，
#: 而 [a5] 第六章明确要求大文件外置后湖仓保留「文件大小、**脱敏标记**、校验和、归属 data_id」四项。
CONTRACT_REDACTION_FLAGS: dict[RedactionStage, str] = {
    RedactionStage.VEHICLE_SIMPLE: "vehicle_desensitized_flag",
    RedactionStage.CLOUD_COMPLEX: "cloud_compliance_decrypted_flag",
}


@dataclass(frozen=True, slots=True)
class ComplianceMarks:
    """一个文件携带的双合规标记。

    [a8] 第六章：「文件需携带『车端脱敏 + 合规云脱密』双合规标记，缺失即合规风险」，
    处置为 **P0 拒绝入湖**——门禁在这里承担的是合规核验职责，不是数据质量职责。
    """

    vehicle: RedactionMark | None = None
    cloud: RedactionMark | None = None

    @classmethod
    def both_applied(
        cls,
        *,
        vehicle_operator: str,
        cloud_operator: str,
        vehicle_rules: Iterable[str] = ("face_blur", "plate_blur", "geo_desensitize"),
        cloud_rules: Iterable[str] = ("sensitive_poi_delete", "bridge_height_blur"),
        moment: datetime | None = None,
    ) -> ComplianceMarks:
        """构造一对「都已执行」的标记。

        默认规则取 [a8] 原文措辞：车端为「人脸 / 车牌等个性脱敏 + 地信脱敏」，
        合规云为「删除敏感 POI、模糊桥梁限高等具体数值」。
        """
        now = moment or datetime.now()
        return cls(
            vehicle=RedactionMark(
                RedactionStage.VEHICLE_SIMPLE, True, vehicle_operator, now, tuple(vehicle_rules)
            ),
            cloud=RedactionMark(
                RedactionStage.CLOUD_COMPLEX, True, cloud_operator, now, tuple(cloud_rules)
            ),
        )

    def missing_stages(self) -> tuple[RedactionStage, ...]:
        """返回缺失或未执行的脱敏段。"""
        missing: list[RedactionStage] = []
        if self.vehicle is None or not self.vehicle.applied:
            missing.append(RedactionStage.VEHICLE_SIMPLE)
        if self.cloud is None or not self.cloud.applied:
            missing.append(RedactionStage.CLOUD_COMPLEX)
        return tuple(missing)

    def validate(self) -> list[str]:
        """双标记完整性校验，返回违规描述列表；空列表表示双脱敏完整。"""
        problems: list[str] = []
        for stage, mark in (
            (RedactionStage.VEHICLE_SIMPLE, self.vehicle),
            (RedactionStage.CLOUD_COMPLEX, self.cloud),
        ):
            if mark is None:
                problems.append(f"缺少{REDACTION_DEFINITIONS[stage].label}标记")
                continue
            if mark.stage is not stage:
                problems.append(f"标记错位：{stage.value} 槽位里放的是 {mark.stage.value}")
                continue
            problems.extend(mark.validate())
        return problems

    @property
    def is_complete(self) -> bool:
        """双脱敏是否齐备——门禁 P0 检查项「脱敏标记完整性」的判据。"""
        return not self.validate()

    def to_meta(self) -> dict[str, Any]:
        """展平为可随 Kafka 元信息流转的扁平字典。

        同时给出两套键名，缺一不可：

        · ``redaction_*``  —— 入湖侧的完整标记（执行方 / 时间 / 生效规则），
          是合规审计与 Flink SQL 门禁条件的判据，比湖表宽；
        · ``*_flag``       —— 共享契约 ``ods_data_file_meta`` 的真实列名
          （见 ``CONTRACT_REDACTION_FLAGS``）。只有带上它们，双脱敏标记才能真正
          落进湖表；否则 ``rows.project_to_table`` 会把整组标记投影掉，
          下游既查不到「这个文件脱没脱敏」，契约侧的 QG-OSS-001 也会取到 NULL。
        """
        out: dict[str, Any] = {
            "redaction_vehicle_applied": bool(self.vehicle and self.vehicle.applied),
            "redaction_cloud_applied": bool(self.cloud and self.cloud.applied),
            CONTRACT_REDACTION_FLAGS[RedactionStage.VEHICLE_SIMPLE]: bool(
                self.vehicle and self.vehicle.applied
            ),
            CONTRACT_REDACTION_FLAGS[RedactionStage.CLOUD_COMPLEX]: bool(
                self.cloud and self.cloud.applied
            ),
        }
        if self.vehicle:
            out["redaction_vehicle_operator"] = self.vehicle.operator
            out["redaction_vehicle_rules"] = ",".join(self.vehicle.rules)
            out["redaction_vehicle_time"] = self.vehicle.applied_at
        if self.cloud:
            out["redaction_cloud_operator"] = self.cloud.operator
            out["redaction_cloud_rules"] = ",".join(self.cloud.rules)
            out["redaction_cloud_time"] = self.cloud.applied_at
        return out

    @classmethod
    def from_meta(cls, meta: dict[str, Any]) -> ComplianceMarks:
        """从扁平字典还原（Kafka 消息 → 标记对象）。缺字段即视为未执行。

        两套键名都认：优先读入湖侧的 ``redaction_*_applied``，没有时回落到共享契约的
        ``*_flag`` 列名——从湖表回读一行重建标记时走的就是后者。
        """

        def _mark(stage: RedactionStage, prefix: str) -> RedactionMark | None:
            contract_flag = CONTRACT_REDACTION_FLAGS[stage]
            raw_applied = meta.get(f"redaction_{prefix}_applied")
            if raw_applied is None:
                raw_applied = meta.get(contract_flag)
            applied = bool(raw_applied)
            operator = meta.get(f"redaction_{prefix}_operator") or ""
            raw_rules = meta.get(f"redaction_{prefix}_rules") or ""
            rules = tuple(r for r in str(raw_rules).split(",") if r)
            moment = meta.get(f"redaction_{prefix}_time")
            if isinstance(moment, str):
                try:
                    moment = datetime.fromisoformat(moment)
                except ValueError:
                    moment = None
            if not applied and not operator and not rules:
                return None
            return RedactionMark(stage, applied, operator, moment, rules)

        return cls(
            vehicle=_mark(RedactionStage.VEHICLE_SIMPLE, "vehicle"),
            cloud=_mark(RedactionStage.CLOUD_COMPLEX, "cloud"),
        )


# --------------------------------------------------------------------------- 合规云架构


@dataclass(frozen=True, slots=True)
class ComplianceCloudTopology:
    """合规云的三条架构约束（[a8] 第四章）。

    1. **独立云**：合规云是具备合规资质的公司，在智驾公司所在云端划分的独立云区域，
       由合规方管理；
    2. **同一云端 VPC**：合规云与智驾云必须在同一云端、同一 VPC 内——数据副本与
       元信息通过内网流转，不出公网、不落第三方；
    3. **不对外暴露**：合规云对象存储无任何对外访问入口，只有脱敏脱密后的数据副本与
       文件元信息可以离开。

    这三条同时满足「物理上隔离」与「链路上高效」。
    """

    #: 合规云所在云厂商 region（与智驾云必须同一云端）
    compliance_cloud_region: str
    adas_cloud_region: str
    #: VPC 标识（必须相同）
    compliance_cloud_vpc_id: str
    adas_cloud_vpc_id: str
    #: 合规云由合规方管理（不是智驾公司自管）
    managed_by_compliance_provider: bool = True
    #: 合规方是否具备合规资质
    provider_qualified: bool = True
    #: 合规云对象存储是否存在对外访问入口（必须为 False）
    object_store_public_endpoint: bool = False
    #: 数据副本与元信息是否走内网（必须为 True：不出公网、不落第三方）
    intranet_only: bool = True
    #: 合规云对象存储的 bucket。声明出来，门禁才能把「file_path 指向合规云」这件事
    #: 说成边界违规而不是「bucket 不在白名单里」——见 ``oss.validate_object_key``。
    compliance_cloud_bucket: str = ""
    #: 智驾云 OSS 的 bucket（合规数据副本的落点）。留空则取 ``settings().minio.raw_bucket``。
    adas_cloud_bucket: str = ""

    def validate(self) -> list[str]:
        """返回违反的架构约束；空列表表示三条约束全部满足。"""
        problems: list[str] = []
        if not self.provider_qualified:
            problems.append("独立云：合规方不具备合规资质")
        if not self.managed_by_compliance_provider:
            problems.append("独立云：合规云必须由合规方管理，不能由智驾公司自管")
        if self.compliance_cloud_region != self.adas_cloud_region:
            problems.append(
                f"同一云端：合规云 region={self.compliance_cloud_region} "
                f"与智驾云 region={self.adas_cloud_region} 不一致"
            )
        if self.compliance_cloud_vpc_id != self.adas_cloud_vpc_id:
            problems.append(
                f"同一 VPC：合规云 vpc={self.compliance_cloud_vpc_id} "
                f"与智驾云 vpc={self.adas_cloud_vpc_id} 不一致，数据副本会走公网"
            )
        if self.object_store_public_endpoint:
            problems.append("不对外暴露：合规云对象存储存在对外访问入口")
        if not self.intranet_only:
            problems.append("不对外暴露：数据副本与元信息未限定内网流转（不出公网、不落第三方）")
        if (
            self.compliance_cloud_bucket
            and self.adas_cloud_bucket
            and self.compliance_cloud_bucket == self.adas_cloud_bucket
        ):
            problems.append(
                "物理上隔离：合规云与智驾云共用同一个 bucket "
                f"{self.compliance_cloud_bucket!r}，原始与未脱密数据就没有「止步于合规云」的落点"
            )
        return problems

    def assert_valid(self) -> None:
        """校验不过直接抛——合规靠架构边界，不靠流程承诺。"""
        problems = self.validate()
        if problems:
            raise ComplianceViolation(
                f"合规云架构约束（共 {COMPLIANCE_CLOUD_CONSTRAINTS} 条）未满足",
                violations=problems,
            )

    def assert_not_publicly_exposed(self) -> None:
        """第 ② 步「上传至合规云对象存储（**不对外暴露**）」的准入条件。

        三条架构约束里的第 3 条在第 ② 步就已经生效：硬盘数据上传进合规云的那一刻，
        对象存储就不能有对外访问入口。把它留到第 ④ 步才查，等于允许数据先在一个
        暴露的桶里躺两天。

        Raises:
            ComplianceViolation: 合规云对象存储存在对外访问入口。
        """
        if self.object_store_public_endpoint:
            raise ComplianceViolation(
                "第 2 步「合规室上传」的目的地必须是不对外暴露的合规云对象存储，"
                "当前拓扑声明了对外访问入口"
            )


class CrossBoundaryPayload(str, Enum):
    """允许跨越「合规云 → 智驾云」边界的载荷类型。

    [a8] 第二章：「进入智驾云的只有『合规数据副本 + 文件元信息』。原始硬盘数据的
    完整生命周期止步于合规云」。
    """

    COMPLIANT_DATA_COPY = "合规数据副本"
    FILE_META = "文件元信息"
    #: 以下两类**禁止**出合规云，登记出来是为了让拦截有明确的枚举而不是靠 else 分支
    RAW_DISK_DATA = "原始硬盘数据"
    UNREDACTED_DATA = "未脱密数据"

    @property
    def may_cross(self) -> bool:
        return self in (CrossBoundaryPayload.COMPLIANT_DATA_COPY, CrossBoundaryPayload.FILE_META)


#: 第 ④ 步「合规数据分发」默认过境的载荷集合。[a8] 第二章表格第 ④ 行逐字：
#: 「合规数据副本复制至智驾云 OSS，**同时**向业务方 Kafka 发送文件元信息（同一 VPC 流转）」——
#: 两件事在同一步里发生，所以默认值是两者，而不是任选其一。
DISTRIBUTION_PAYLOADS: tuple[CrossBoundaryPayload, ...] = (
    CrossBoundaryPayload.COMPLIANT_DATA_COPY,
    CrossBoundaryPayload.FILE_META,
)


def assert_may_cross_boundary(payload: CrossBoundaryPayload) -> None:
    """守住合规云的唯一合法出口。

    每一次「合规云 → 智驾云」的分发都要过这道判断：``ComplianceChain.advance()``
    在第 ④ 步对 ``payloads`` 里的每一类载荷逐个调用它，因此拦截分支是链路上的真实
    闸门，而不只是一个供外部调用的公开断言。

    Raises:
        ComplianceViolation: 载荷类型不允许离开合规云。
    """
    if not payload.may_cross:
        raise ComplianceViolation(
            f"{payload.value}不得离开合规云——原始数据的完整生命周期止步于合规云，"
            "智驾云从第一步就只接触脱敏后的数据"
        )


# --------------------------------------------------------------------------- 链路状态机


@dataclass(slots=True)
class ComplianceChain:
    """五步合规链路状态机：一个 data_id（一个 clip）一条链路实例。

    用法::

        chain = ComplianceChain(data_id="COLLECT_BP_20260301123045_b7e2")
        chain.advance(ComplianceStep.VEHICLE_REDACTION, marks=marks)
        chain.advance(ComplianceStep.COMPLIANCE_ROOM_UPLOAD, operator="合规员 A")
        chain.advance(ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION, marks=marks)
        chain.advance(ComplianceStep.COMPLIANT_DATA_DISTRIBUTION, topology=topo)
        chain.assert_ready_for_ingest()          # 第 ⑤ 步之前的硬闸门

    约束：
      · 顺序不能乱（step N 之前 step N-1 必须已完成）；
      · 第 ① 步必须落下车端脱敏标记，第 ③ 步必须落下合规云脱密标记；
      · 第 ② 步的上传目的地必须是不对外暴露的合规云对象存储（拓扑已知时生效）；
      · 第 ④ 步必须通过合规云架构约束校验（同一 VPC / 独立云 / 不对外暴露），
        且过境载荷只能是「合规数据副本 + 文件元信息」两类。
    """

    data_id: str
    completed: dict[ComplianceStep, datetime] = field(default_factory=dict)
    marks: ComplianceMarks = field(default_factory=ComplianceMarks)
    topology: ComplianceCloudTopology | None = None
    #: 每步的操作人/执行方，合规审计用
    operators: dict[ComplianceStep, str] = field(default_factory=dict)
    #: 第 ④ 步实际过境的载荷类型，合规审计留痕（默认两类，见 DISTRIBUTION_PAYLOADS）
    crossed_payloads: tuple[CrossBoundaryPayload, ...] = ()

    # ---- 推进 ----

    def advance(
        self,
        step: ComplianceStep,
        *,
        marks: ComplianceMarks | None = None,
        topology: ComplianceCloudTopology | None = None,
        operator: str = "",
        moment: datetime | None = None,
        payloads: Iterable[CrossBoundaryPayload] = DISTRIBUTION_PAYLOADS,
    ) -> ComplianceChain:
        """推进到某一步，顺序与前置条件不满足即抛 ``ComplianceViolation``。

        Args:
            step: 要完成的步骤。
            marks: 第 ① / ③ 步落下的脱敏标记（合并入链路持有的标记）。
            topology: 第 ④ 步的合规云架构拓扑，用于校验同 VPC / 独立云 / 不对外暴露。
            operator: 执行方，合规审计留痕。
            moment: 完成时间，缺省取当前时间。
            payloads: 第 ④ 步实际过境的载荷类型，逐个过 ``assert_may_cross_boundary``。
                缺省是 [a8] 规定的两类（合规数据副本 + 文件元信息）；调用方把原始硬盘
                数据或未脱密数据混进来，这一步就会抛——合规云的合法出口只有一条。

        Returns:
            self，便于链式调用。
        """
        if step in self.completed:
            raise ComplianceViolation(f"第 {step.value} 步「{step.label}」已完成，不可重复推进")
        previous = [s for s in ComplianceStep if s.value < step.value]
        missing = [s for s in previous if s not in self.completed]
        if missing:
            raise ComplianceViolation(
                f"五步合规链路顺序不能乱：要做第 {step.value} 步「{step.label}」，但前置步骤未完成",
                violations=[
                    f"第 {s.value} 步「{s.label}」（{s.site.value}）未完成" for s in missing
                ],
            )

        if marks is not None:
            self._merge_marks(marks)
        if topology is not None:
            self.topology = topology

        if step is ComplianceStep.VEHICLE_REDACTION:
            self._require_stage(RedactionStage.VEHICLE_SIMPLE, step)
        elif step is ComplianceStep.COMPLIANCE_ROOM_UPLOAD:
            # ② 合规室上传：「上传至合规云对象存储（不对外暴露）」——拓扑已知就在这一步查，
            # 不等到第 ④ 步。拓扑未知时不拦（智驾云侧可能只拿到「已上传」的回执）。
            if self.topology is not None:
                self.topology.assert_not_publicly_exposed()
        elif step is ComplianceStep.COMPLIANCE_CLOUD_DESENSITIZATION:
            self._require_stage(RedactionStage.CLOUD_COMPLEX, step)
        elif step is ComplianceStep.COMPLIANT_DATA_DISTRIBUTION:
            if self.topology is None:
                raise ComplianceViolation(
                    "第 4 步「合规数据分发」必须声明合规云架构拓扑（同一云端、同一 VPC 内网流转）"
                )
            self.topology.assert_valid()
            crossing = tuple(payloads)
            if not crossing:
                raise ComplianceViolation(
                    "第 4 步「合规数据分发」至少要过境一类载荷（合规数据副本 / 文件元信息）"
                )
            for payload in crossing:
                assert_may_cross_boundary(payload)
            self.crossed_payloads = crossing

        self.completed[step] = moment or datetime.now()
        if operator:
            self.operators[step] = operator
        return self

    def _merge_marks(self, marks: ComplianceMarks) -> None:
        self.marks = ComplianceMarks(
            vehicle=marks.vehicle or self.marks.vehicle,
            cloud=marks.cloud or self.marks.cloud,
        )

    def _require_stage(self, stage: RedactionStage, step: ComplianceStep) -> None:
        mark = self.marks.vehicle if stage is RedactionStage.VEHICLE_SIMPLE else self.marks.cloud
        definition = REDACTION_DEFINITIONS[stage]
        if mark is None or not mark.applied:
            raise ComplianceViolation(
                f"第 {step.value} 步「{step.label}」必须落下{definition.label}标记：{definition.content}"
            )
        problems = mark.validate()
        if problems:
            raise ComplianceViolation(f"{definition.label}标记不完整", violations=problems)

    # ---- 查询 ----

    @property
    def current_step(self) -> ComplianceStep | None:
        """已完成的最后一步；尚未开始时为 None。"""
        return max(self.completed, default=None)

    @property
    def is_complete(self) -> bool:
        return len(self.completed) == COMPLIANCE_CHAIN_STEPS

    def ready_for_ingest(self) -> list[str]:
        """第 ⑤ 步「实时入湖」的前置检查，返回未满足项。"""
        problems: list[str] = []
        for step in ComplianceStep:
            if step is ComplianceStep.REALTIME_INGEST:
                continue
            if step not in self.completed:
                problems.append(f"第 {step.value} 步「{step.label}」（{step.site.value}）未完成")
        problems.extend(self.marks.validate())
        if self.topology is not None:
            problems.extend(self.topology.validate())
        return problems

    def assert_ready_for_ingest(self) -> None:
        """入湖前的硬闸门，不满足直接抛。"""
        problems = self.ready_for_ingest()
        if problems:
            raise ComplianceViolation(
                f"data_id={self.data_id} 未走完合规处理段与分发段，不得进入第 5 步「实时入湖」",
                violations=problems,
            )

    def elapsed_seconds(self) -> float | None:
        """全链路耗时（秒）：第 ① 步到最后一步。未完成两步以上时返回 None。

        ⚠️ 原文未明确，本项目设计：原文没有给链路耗时的目标值或 SLA，
        这里只提供度量口径，不设阈值——闭环耗时的口径归 DWS 层的效率指标表。
        """
        if len(self.completed) < 2:
            return None
        first = min(self.completed.values())
        last = max(self.completed.values())
        return (last - first).total_seconds()

    def to_audit_row(self) -> dict[str, Any]:
        """展平为一行合规审计记录，可随元信息一起流转。"""
        row: dict[str, Any] = {
            "data_id": self.data_id,
            "compliance_chain_step": self.current_step.value if self.current_step else 0,
            "compliance_chain_complete": self.is_complete,
            "compliance_status": "compliant" if not self.ready_for_ingest() else "pending",
            "crossed_payloads": ",".join(p.value for p in self.crossed_payloads),
            # 链路耗时口径：车端脱敏到最后一步的墙钟秒数。阈值不在这里定——
            # 闭环耗时的 SLA 归 DWS 层的效率指标表，本行只负责把事实带出去。
            "chain_elapsed_sec": self.elapsed_seconds(),
        }
        for step in ComplianceStep:
            moment = self.completed.get(step)
            row[f"step{step.value}_time"] = moment
            row[f"step{step.value}_operator"] = self.operators.get(step, "")
        row.update(self.marks.to_meta())
        return row
