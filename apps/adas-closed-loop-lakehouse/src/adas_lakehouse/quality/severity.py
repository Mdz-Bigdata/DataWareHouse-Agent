"""门禁的分级体系：严重程度 / 处置结果 / 六维 / 五层 / 三道防线 / 四档异常等级。

原文（第 6 篇）的核心设计是「严重程度决定处置」，检查器只输出三种结果：

    ERROR → REJECT（拒绝入湖） | WARNING → ALLOW_WITH_FLAG（带标记放行） | 通过 → ACCEPTED

这个模块把原文的五张表——五层质量问题全景、六维检查框架、三道防线、四档 SLA、
三通道——全部落成可枚举、可查询的结构，供规则声明直接引用，避免散落在注释里。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from .thresholds import (
    P0_RESPONSE_MINUTES,
    P1_FIX_WITHIN_DAYS,
    P1_RESPONSE_HOURS,
    P2_CLOSE_BUSINESS_DAYS,
    P3_CONSECUTIVE_WEEKS_TO_ESCALATE,
)

__all__ = [
    "Severity",
    "Disposition",
    "QualityDimension",
    "QualityLayer",
    "DefenseLine",
    "Channel",
    "IssueLevel",
    "LevelPolicy",
    "LEVEL_POLICIES",
    "DISPOSITION_BY_SEVERITY",
    "QUALITY_FLAG_FIELD",
]

#: 带标记放行的数据写入的字段名（原文第二章：「带标记放行的数据写入 _quality_flag，
#: 供下游按质量筛选，不阻塞主链路」）。
#: ⚠️ 原文未明确，本项目设计：该字段不属于 catalog.spec 自动追加的系统字段
#: （那里只有 _ingest_time/_source_system/update_time），由入湖作业在写 ODS 时附加，
#: 取值格式见 gate.GateDecision.quality_flag。
QUALITY_FLAG_FIELD = "_quality_flag"


class Severity(str, Enum):
    """规则严重程度。原文只有两档——检查器的三种出口里，第三种是「没命中任何规则」。"""

    ERROR = "ERROR"
    WARNING = "WARNING"


class Disposition(str, Enum):
    """门禁处置结果（原文第二章「检查器三分支」）。"""

    REJECT = "REJECT"
    ALLOW_WITH_FLAG = "ALLOW_WITH_FLAG"
    ACCEPTED = "ACCEPTED"


#: 严重程度 → 处置：原文「ERROR → REJECT | WARNING → ALLOW_WITH_FLAG」。
#: 这张表是门禁唯一的处置决策依据，业务代码里不允许再写 if severity == ...。
DISPOSITION_BY_SEVERITY: dict[Severity, Disposition] = {
    Severity.ERROR: Disposition.REJECT,
    Severity.WARNING: Disposition.ALLOW_WITH_FLAG,
}


@dataclass(frozen=True, slots=True)
class _Dimension:
    key: str
    name_cn: str
    checkpoints: str
    policy_cn: str


class QualityDimension(Enum):
    """六维质量检查框架（原文第二章表格，三个通道通用）。

    每一维不仅定义「查什么」，更定义「查出来怎么办」——处置策略和检查规则同等重要。
    """

    COMPLETENESS = _Dimension(
        "completeness",
        "完整性",
        "必填字段非空、记录不缺条、批次不缺片、多模态不缺帧",
        "拒绝入湖（硬规则）",
    )
    ACCURACY = _Dimension(
        "accuracy",
        "准确性",
        "数值范围合法、时间戳真实合理、定位无跳变",
        "拒绝入湖 / 告警标记",
    )
    CONSISTENCY = _Dimension(
        "consistency",
        "一致性",
        "跨源关联存在性、状态不冲突、元信息与文件本体一致",
        "告警标记，允许入湖",
    )
    UNIQUENESS = _Dimension(
        "uniqueness",
        "唯一性",
        "主键 / 事件 ID 不重复，幂等去重，近重复场景标记",
        "自动去重，重复率超限告警",
    )
    VALIDITY = _Dimension(
        "validity",
        "有效性",
        "枚举值在字典内、格式符合规范、状态流转合法、文件可解码",
        "拒绝入湖（硬规则）/ 告警",
    )
    TIMELINESS = _Dimension(
        "timeliness",
        "及时性",
        "入湖延迟、CDC 同步延迟、批次到达完整性",
        "监控告警（不拦截）",
    )

    @property
    def key(self) -> str:
        return self.value.key

    @property
    def name_cn(self) -> str:
        return self.value.name_cn

    @property
    def checkpoints(self) -> str:
        return self.value.checkpoints

    @property
    def policy_cn(self) -> str:
        return self.value.policy_cn

    @classmethod
    def by_key(cls, key: str) -> QualityDimension:
        for d in cls:
            if d.key == key:
                return d
        raise KeyError(f"未知质量维度: {key!r}；可选 {[d.key for d in cls]}")


@dataclass(frozen=True, slots=True)
class _Layer:
    key: str
    name_cn: str
    typical_issues: str
    gate_action: str


class QualityLayer(Enum):
    """五层质量问题全景（原文第一章表格），按产生环节划分。

    注意右列的差异：并不是所有问题都靠门禁拦截——L4 只告警反哺补采，不阻断入湖。
    """

    L1_SENSOR = _Layer(
        "L1",
        "传感器与原始数据",
        "时间同步误差、标定漂移、过曝/模糊、点云丢点、丢帧",
        "丢帧、同步超限硬拒绝；空帧拒绝",
    )
    L2_FLEET_RETURN = _Layer(
        "L2",
        "量产车回传与筛选",
        "触发偏差、片段截断、压缩损伤、脱敏损伤、元数据缺失",
        "损坏文件与关键元数据缺失拒绝；近重复抑制",
    )
    L3_ANNOTATION = _Layer(
        "L3",
        "数据标注",
        "标注框误差、漏标误标、时序 ID 跳变、自动标注噪声",
        "未过质检不入湖；自动标注未审核拒绝",
    )
    L4_DISTRIBUTION = _Layer(
        "L4",
        "数据分布与场景",
        "长尾场景不足、类别不均衡、数据漂移、Sim2Real 差距",
        "分布告警（不拦截），反哺补采",
    )
    L5_ENGINEERING = _Layer(
        "L5",
        "数据工程与训练评测",
        "数据泄露、近重复、血缘缺失、评测集偏差、真值噪声",
        "跨集泄漏拒绝；血缘缺失告警",
    )

    @property
    def key(self) -> str:
        return self.value.key

    @property
    def name_cn(self) -> str:
        return self.value.name_cn

    @property
    def gate_action(self) -> str:
        return self.value.gate_action

    @classmethod
    def by_key(cls, key: str) -> QualityLayer:
        for layer in cls:
            if layer.key == key.upper():
                return layer
        raise KeyError(f"未知质量层级: {key!r}；可选 {[x.key for x in cls]}")


class DefenseLine(str, Enum):
    """治理三道防线（原文第一章）。门禁只是第一道，边界必须清晰。

    · GATE            入湖门禁：规则化硬检查，入湖瞬间可判定的问题（格式、非空、合规标记）
    · AUTO_QC         入湖后自动质检与抽检：算法驱动（图像质量评分、标注精度抽检），
                      质检结论回写 ods_qc_result
    · DISTRIBUTION    分布监控与数据治理：场景覆盖度看板、漂移告警，驱动挖掘域补采

    图像过曝、标注精度这类需要算法评分的问题不归门禁——本模块只实现 GATE。
    """

    GATE = "gate"
    AUTO_QC = "auto_qc"
    DISTRIBUTION = "distribution_monitor"


class Channel(str, Enum):
    """三通道入湖（系列二前几篇）+ 一个跨通道的公共集合。

    原文第四章的规律：业务库查「流程合规」（质检、审核、防泄漏），
    事件流查「时空合理」（时间戳、截断、重复），文件查「物理完整」（脱敏、缺帧、同步）。
    """

    MYSQL_CDC = "mysql_cdc"
    KAFKA = "kafka"
    OSS_FILE = "oss_file"
    COMMON = "common"

    @property
    def name_cn(self) -> str:
        return {
            Channel.MYSQL_CDC: "MySQL CDC · 业务库数据",
            Channel.KAFKA: "Kafka 消息流 · 事件数据",
            Channel.OSS_FILE: "OSS 采集文件",
            Channel.COMMON: "三通道通用",
        }[self]


@dataclass(frozen=True, slots=True)
class LevelPolicy:
    """一档异常等级的完整策略（原文第五章「四个异常等级对应四档响应 SLA」）。

    notify_channels 是告警触达方式，response_target 是响应时限（秒），
    closure_target 是闭环时限（秒，None 表示原文只给了汇总节奏没给闭环时限）。
    """

    level: str
    name_cn: str
    typical_cases: str
    notify_channels: tuple[str, ...]
    response_requirement_cn: str
    response_target_seconds: int | None
    closure_target_seconds: int | None
    hard_block: bool

    def response_due_at(self, detected_at: datetime) -> datetime | None:
        """按 SLA 算响应到期时间；原文未给时限的等级返回 None。"""
        if self.response_target_seconds is None:
            return None
        return detected_at + timedelta(seconds=self.response_target_seconds)

    def closure_due_at(self, detected_at: datetime) -> datetime | None:
        """按 SLA 算闭环到期时间。

        ⚠️ 原文未明确，本项目设计：P2 的「3 个工作日」这里按自然日近似换算，
        不接企业节假日日历；接入日历后应覆盖本方法。
        """
        if self.closure_target_seconds is None:
            return None
        if self.level == "P1":
            # 「当日修复」：当天 23:59:59 之前
            day: date = detected_at.date()
            return datetime.combine(day, datetime.max.time()).replace(
                microsecond=0, tzinfo=detected_at.tzinfo
            )
        return detected_at + timedelta(seconds=self.closure_target_seconds)


class IssueLevel(str, Enum):
    """四档异常等级。级别决定告警方式与 SLA，与 Severity 正交：

    Severity 决定「拦不拦」（ERROR→REJECT / WARNING→带标记放行），
    IssueLevel 决定「拦下来之后多快处理、怎么通知」。

    P0 是合规/安全级，硬拦截且不可降级、不可灰度关闭——见 rules.RuleSpec.__post_init__。
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def policy(self) -> LevelPolicy:
        return LEVEL_POLICIES[self]

    @property
    def is_compliance(self) -> bool:
        """是否为合规/安全级（P0）——门禁里唯一不允许软化的等级。"""
        return self is IssueLevel.P0

    def escalate(self) -> IssueLevel:
        """升一级。P0 已是最高，返回自身。"""
        order = [IssueLevel.P3, IssueLevel.P2, IssueLevel.P1, IssueLevel.P0]
        idx = order.index(self)
        return order[min(idx + 1, len(order) - 1)]


#: 四档响应 SLA（原文第五章表格，逐字落地）。
LEVEL_POLICIES: dict[IssueLevel, LevelPolicy] = {
    IssueLevel.P0: LevelPolicy(
        level="P0",
        name_cn="合规级",
        typical_cases="脱敏标记缺失、主键为空、Schema 不可解析、多模态整帧缺失",
        notify_channels=("电话", "钉钉"),
        response_requirement_cn=f"电话 + 钉钉，{P0_RESPONSE_MINUTES} 分钟内响应",
        response_target_seconds=P0_RESPONSE_MINUTES * 60,  # 30 分钟
        closure_target_seconds=None,  # 原文只给响应时限
        hard_block=True,
    ),
    IssueLevel.P1: LevelPolicy(
        level="P1",
        name_cn="严重",
        typical_cases="必填字段缺失、ID 格式非法、文件损坏不可解码、跨集泄漏",
        notify_channels=("钉钉", "工单"),
        response_requirement_cn=f"钉钉 + 工单，{P1_RESPONSE_HOURS} 小时内响应，当日修复",
        response_target_seconds=P1_RESPONSE_HOURS * 3600,  # 2 小时
        closure_target_seconds=P1_FIX_WITHIN_DAYS * 86400 or 86400,  # 当日修复
        hard_block=True,
    ),
    IssueLevel.P2: LevelPolicy(
        level="P2",
        name_cn="一般",
        typical_cases="关联缺失、状态流转异常、时间同步超限、定位跳变",
        notify_channels=("日报",),
        response_requirement_cn=f"日报汇总，{P2_CLOSE_BUSINESS_DAYS} 个工作日内闭环",
        response_target_seconds=None,
        closure_target_seconds=P2_CLOSE_BUSINESS_DAYS * 86400,  # 3 个工作日（自然日近似）
        hard_block=False,
    ),
    IssueLevel.P3: LevelPolicy(
        level="P3",
        name_cn="观察",
        typical_cases="入湖延迟、重复率波动、回传分布偏离、批次轻微缺片",
        notify_channels=("周报",),
        response_requirement_cn=(
            f"周报汇总，连续{'两' if P3_CONSECUTIVE_WEEKS_TO_ESCALATE == 2 else P3_CONSECUTIVE_WEEKS_TO_ESCALATE}"
            f"周超标升级 P2"
        ),
        response_target_seconds=None,
        closure_target_seconds=None,
        hard_block=False,
    ),
}
