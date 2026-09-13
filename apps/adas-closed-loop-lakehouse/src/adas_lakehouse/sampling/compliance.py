"""抽帧前置脱敏校验：合规红线在挖掘侧再设一道闸。

原文五章第 4 个实现要点，逐字：
  "抽帧任务启动前校验文件已完成双脱敏（复用合规链路的脱敏标记），
   未脱敏数据一律拒绝抽帧——合规红线在挖掘侧再设一道闸。"

原文一章同时划定了边界：
  "采集车上传的 clip 与采集标签，沿用系列二讲过的既有合规链路与入湖通道：
   车端脱敏 → 合规云脱密 → 智驾云入湖"
  "抽帧引擎的输入只有湖仓里的合规数据，不存在「绕过合规直接抽帧」的路径"

「双脱敏」= 车端脱敏 + 合规云脱密两道，本模块把它建模成两个必须同时为真的标记。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "DesensitizationStage",
    "DesensitizationStatus",
    "ComplianceRejectedError",
    "ClipComplianceMarks",
    "check_desensitization",
    "require_desensitized",
]


class DesensitizationStage(str, Enum):
    """合规链路的两道脱敏（原文一章："车端脱敏 → 合规云脱密 → 智驾云入湖"）。"""

    VEHICLE_SIDE = "vehicle_side"
    COMPLIANCE_CLOUD = "compliance_cloud"

    @property
    def name_cn(self) -> str:
        return {
            DesensitizationStage.VEHICLE_SIDE: "车端脱敏",
            DesensitizationStage.COMPLIANCE_CLOUD: "合规云脱密",
        }[self]


#: 「双脱敏」必须集齐的两道，缺一不可
REQUIRED_STAGES: tuple[DesensitizationStage, ...] = (
    DesensitizationStage.VEHICLE_SIDE,
    DesensitizationStage.COMPLIANCE_CLOUD,
)


class DesensitizationStatus(str, Enum):
    """前置校验结论，落 dwd_mining_image_frame_detail.desensitize_status
    （内存侧 FrameRecord.desensitization_status，列名以 catalog.registry 为准）。

    ⚠️ 原文未明确，本项目设计：原文只说 "复用合规链路的脱敏标记"，没给标记的字面量。
    本项目定义三态，其中 PASSED 是唯一允许抽帧的状态。
    """

    PASSED = "double_desensitized"
    REJECTED = "rejected_not_desensitized"
    UNKNOWN = "unknown"


class ComplianceRejectedError(RuntimeError):
    """未通过双脱敏校验——一律拒绝抽帧，不降级、不放行。

    Attributes:
        data_id: 被拒绝的 clip 锚点。
        missing: 缺失的脱敏环节。
    """

    def __init__(
        self, data_id: str, missing: tuple[DesensitizationStage, ...], detail: str = ""
    ) -> None:
        self.data_id = data_id
        self.missing = missing
        names = "、".join(s.name_cn for s in missing) or "未知"
        msg = f"未脱敏数据一律拒绝抽帧：data_id={data_id} 缺少[{names}]"
        if detail:
            msg += f"；{detail}"
        super().__init__(msg)


@dataclass(frozen=True, slots=True)
class ClipComplianceMarks:
    """从合规链路读到的脱敏标记快照。

    字段来源于采集域既有表（原文一章："clip 的元数据完全复用采集域既有表"）：
      · dwd_collect_clip_detail.compliance_status
      · ods_data_file_meta（逐文件的脱敏标记）

    ⚠️ 原文未明确，本项目设计：采集域表里 compliance_status 的取值枚举原文没给，
    本项目按「两个布尔标记 + 一个原始状态串」建模，由适配层负责把各项目的实际
    取值映射成这两个布尔值，避免把某一家的字面量写死进挖掘侧。
    """

    data_id: str
    vehicle_side_desensitized: bool = False
    compliance_cloud_declassified: bool = False
    #: 采集域原样带过来的状态串，仅用于告警可读性，不参与判定
    raw_compliance_status: str = ""

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ClipComplianceMarks:
        """从湖仓查询结果行构造。缺字段按「未脱敏」处理——默认拒绝，不默认放行。"""
        return cls(
            data_id=str(row.get("data_id", "")),
            vehicle_side_desensitized=bool(row.get("vehicle_side_desensitized", False)),
            compliance_cloud_declassified=bool(row.get("compliance_cloud_declassified", False)),
            raw_compliance_status=str(row.get("compliance_status", "") or ""),
        )

    def missing_stages(self) -> tuple[DesensitizationStage, ...]:
        """返回尚未完成的脱敏环节。"""
        missing: list[DesensitizationStage] = []
        if not self.vehicle_side_desensitized:
            missing.append(DesensitizationStage.VEHICLE_SIDE)
        if not self.compliance_cloud_declassified:
            missing.append(DesensitizationStage.COMPLIANCE_CLOUD)
        return tuple(missing)


@dataclass(frozen=True, slots=True)
class ComplianceDecision:
    """校验结论。``allowed`` 为 False 时抽帧任务必须整体不启动。"""

    data_id: str
    allowed: bool
    status: DesensitizationStatus
    missing: tuple[DesensitizationStage, ...] = ()

    @property
    def reason(self) -> str:
        if self.allowed:
            return "双脱敏已完成，允许抽帧"
        names = "、".join(s.name_cn for s in self.missing) or "脱敏标记缺失"
        return f"未完成[{names}]，拒绝抽帧"


def check_desensitization(
    marks: ClipComplianceMarks | None, data_id: str = ""
) -> ComplianceDecision:
    """执行前置校验，返回结论但不抛异常（用于批量审计 / 报表）。

    三态而非两态，是为了把「查到了标记、但没脱完」与「压根没查到标记」分开记账：
    前者是合规链路确实没跑完，后者是挖掘侧根本没拿到采集域的标记快照（上游没回填、
    或者 join 丢了）。两者都**一律拒绝抽帧**（原文五章的红线没有例外），但运维要
    去修的东西完全不同——混成一种状态，湖里就再也分不出「谁没脱敏」和「谁没对上账」。

    Args:
        marks: 合规链路的脱敏标记快照；``None`` 表示没拿到快照。
        data_id: 仅在 ``marks`` 为 None 时用于回填结论里的锚点。

    Returns:
        ComplianceDecision，``allowed=True`` 表示双脱敏齐全。
    """
    if marks is None:
        return ComplianceDecision(data_id, False, DesensitizationStatus.UNKNOWN, REQUIRED_STAGES)
    missing = marks.missing_stages()
    if missing:
        return ComplianceDecision(marks.data_id, False, DesensitizationStatus.REJECTED, missing)
    return ComplianceDecision(marks.data_id, True, DesensitizationStatus.PASSED)


def require_desensitized(
    marks: ClipComplianceMarks | None, data_id: str = ""
) -> ComplianceDecision:
    """执行前置校验，未通过直接抛异常——抽帧引擎在任务启动前调用它。

    这是「一律拒绝」的硬实现：没有降级路径、没有 warn-and-continue。
    标记快照缺失（``marks=None``）同样抛异常——默认拒绝，不默认放行。

    Raises:
        ComplianceRejectedError: 双脱敏未齐全，或根本没拿到脱敏标记。
    """
    decision = check_desensitization(marks, data_id)
    if not decision.allowed:
        raise ComplianceRejectedError(
            decision.data_id,
            decision.missing,
            marks.raw_compliance_status if marks is not None else "未取到采集域脱敏标记",
        )
    return decision
