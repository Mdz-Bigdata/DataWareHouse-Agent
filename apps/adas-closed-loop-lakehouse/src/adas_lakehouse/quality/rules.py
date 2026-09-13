"""声明式规则模型与检查器实现。

原文第三章：「门禁规则不写死在代码里，而是按『检查类型 → 表 → 字段』三级组织成
YAML 配置」，「检查器实现非常克制——遍历该表注册的规则，按严重程度决定三种出口」。

因此本模块的硬性约束是：

1. 规则是数据（:class:`RuleSpec`），不是代码分支。新增一条规则 = 加一条声明，
   不改一行 if。
2. 检查逻辑按 :class:`CheckType` 注册进 :data:`CHECKS` 字典，派发靠查表不靠 if-elif。
3. 检查器只回答「命中了没有」，处置由 severity 经 severity.DISPOSITION_BY_SEVERITY
   决定，规则本身不决定拦不拦。

⚠️ 原文未明确，本项目设计：原文说「四类检查各看一个典型例子」，但配置截图未公开，
唯一可辨认的是 data_id 的正则。本项目把四类基础检查定为
NOT_NULL / REGEX / ENUM / RANGE，并按第四章三通道规则的实际需要扩展了若干检查类型
（多模态齐全、时间同步、重复率、跨集泄漏等），扩展部分同样是声明式的。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from dataclasses import field as dc_field  # RuleSpec 有名为 field 的成员，类体内需用别名
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Final

from .severity import (
    Channel,
    IssueLevel,
    QualityDimension,
    QualityLayer,
    Severity,
)

__all__ = [
    "MISSING",
    "CheckType",
    "RuleScope",
    "RepairAction",
    "CheckOutcome",
    "CheckContext",
    "RuleSpec",
    "RuleHit",
    "CHECKS",
    "DuplicateTracker",
    "register_check",
    "extract",
    "when_matches",
]


class _Missing:
    """字段缺失的哨兵。与 ``None`` 区分：``None`` 是「有这个字段但值为空」。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<MISSING>"

    def __bool__(self) -> bool:
        return False


MISSING: Final[_Missing] = _Missing()


class RuleScope(str, Enum):
    """规则作用域。

    · RECORD 逐条记录判定（绝大多数规则）
    · BATCH  批级判定，作用在批统计量上（重复率 > 5%、丢帧率 > 1%、入湖延迟等）
    """

    RECORD = "record"
    BATCH = "batch"


class RepairAction(str, Enum):
    """原文第五章第 ④ 步「分流处置」的三条分支。

    · A 自动修复（重传 / 幂等重放 / 断点续传）
    · B 人工修复（源端补数，工单跟踪）
    · C 弃置归档（无法修复，标记原因后归档保留审计）
    """

    AUTO_REPAIR = "A"
    MANUAL_REPAIR = "B"
    DISCARD_ARCHIVE = "C"

    @property
    def name_cn(self) -> str:
        return {
            RepairAction.AUTO_REPAIR: "A 自动修复（重传 / 幂等重放 / 断点续传）",
            RepairAction.MANUAL_REPAIR: "B 人工修复（源端补数，工单跟踪）",
            RepairAction.DISCARD_ARCHIVE: "C 弃置归档（无法修复，标记原因后归档保留审计）",
        }[self]


class CheckType(str, Enum):
    """检查类型——YAML 三级配置的第一级。

    前四项是原文第三章的「四类检查」（⚠️ 类型名为本项目推断，见模块 docstring），
    其余为落地第四章三通道规则所必需的扩展类型。
    """

    # ---- 四类基础检查 ----
    NOT_NULL = "not_null"  # 完整性：必填字段非空
    REGEX = "regex"  # 有效性：格式符合规范（data_id 三级 ID 规范）
    ENUM = "enum"  # 有效性：枚举值在字典内
    RANGE = "range"  # 准确性：数值范围合法
    # ---- 扩展检查 ----
    ABS_MAX = "abs_max"  # 绝对值上限（时间同步 ≤ ±10ms / ±50ms）
    RATIO_MAX = "ratio_max"  # 比率上限（丢帧率 > 1%、重复率 > 5%）
    MIN_VALUE = "min_value"  # 下限（片段覆盖触发前 ≥ N 秒 / 后 ≥ M 秒）
    TIMESTAMP_WINDOW = "timestamp_window"  # 时间戳合理性（不早于出厂、不晚于服务器+容忍窗口）
    REQUIRED_FLAGS = "required_flags"  # 合规双标记（车端脱敏 + 合规云脱密）
    REQUIRED_MEMBERS = "required_members"  # 多模态齐全（相机/激光雷达/毫米波/IMU/GNSS）
    REFERENCE_EXISTS = "reference_exists"  # 一致性：跨源关联存在性
    STATUS_TRANSITION = "status_transition"  # 有效性：状态流转合法
    SCHEMA_PARSABLE = "schema_parsable"  # 有效性：Schema 可解析（P0）
    DECODABLE = "decodable"  # 有效性：文件可解码 / 校验和一致
    UNIQUE_KEY = "unique_key"  # 唯一性：主键 / 事件 ID 不重复
    SIMILARITY_MAX = "similarity_max"  # 唯一性：近重复场景标记
    DISJOINT_SPLIT = "disjoint_split"  # L5：训练/验证/测试集按 clip 划分，不得跨集
    FRESHNESS = "freshness"  # 及时性：入湖延迟 / CDC 同步延迟
    CUSTOM = "custom"  # 逃生通道：注册具名谓词


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """一次检查的结果。``ok=True`` 表示通过，不产生命中。"""

    ok: bool
    detail: str = ""
    observed: Any = None

    @classmethod
    def passed(cls, observed: Any = None) -> CheckOutcome:
        return cls(True, "", observed)

    @classmethod
    def failed(cls, detail: str, observed: Any = None) -> CheckOutcome:
        return cls(False, detail, observed)


class DuplicateTracker:
    """唯一性检查的去重台账（进程内）。

    真实幂等去重由 Paimon 主键 Upsert 兜底（原文 4.2），门禁这层只负责
    「看见重复」并统计重复率，不负责物理去重。
    """

    __slots__ = ("_seen", "_duplicates", "_total")

    def __init__(self) -> None:
        self._seen: dict[str, set[str]] = {}
        self._duplicates: dict[str, int] = {}
        self._total: dict[str, int] = {}

    def observe(self, table: str, key: str) -> bool:
        """登记一个主键，返回 True 表示这是重复键。"""
        bucket = self._seen.setdefault(table, set())
        self._total[table] = self._total.get(table, 0) + 1
        if key in bucket:
            self._duplicates[table] = self._duplicates.get(table, 0) + 1
            return True
        bucket.add(key)
        return False

    def duplicate_rate(self, table: str) -> float:
        """该表当前累计的重复率，用于 BATCH 域的「重复率 > 5% 告警」。"""
        total = self._total.get(table, 0)
        if total == 0:
            return 0.0
        return self._duplicates.get(table, 0) / total

    def stats(self, table: str) -> dict[str, float]:
        return {
            "record_count": float(self._total.get(table, 0)),
            "duplicate_count": float(self._duplicates.get(table, 0)),
            "duplicate_rate": self.duplicate_rate(table),
        }

    def reset(self, table: str | None = None) -> None:
        if table is None:
            self._seen.clear()
            self._duplicates.clear()
            self._total.clear()
            return
        self._seen.pop(table, None)
        self._duplicates.pop(table, None)
        self._total.pop(table, None)


@dataclass(slots=True)
class CheckContext:
    """检查期上下文：检查器需要但记录本身给不了的东西。

    外部依赖（跨源关联查询、文件探针）全部以可选回调注入，默认 None 时
    对应的检查会「跳过并记为通过」而不是抛异常——门禁绝不能因为旁路依赖
    不可用就把整条入湖链路卡死（原文第六章：门禁自身不能成为瓶颈）。
    """

    table: str
    channel: Channel = Channel.COMMON
    now: datetime = field(default_factory=datetime.now)
    server_time: datetime | None = None
    record: Mapping[str, Any] = field(default_factory=dict)
    batch_stats: Mapping[str, Any] = field(default_factory=dict)
    duplicates: DuplicateTracker = field(default_factory=DuplicateTracker)
    #: (target_table, target_field, value) -> 是否存在。跨源关联存在性检查用。
    reference_lookup: Callable[[str, str, Any], bool] | None = None
    #: data_id -> 已登记的数据集划分（train/val/test），跨集泄漏检查用。
    split_lookup: Callable[[str], str | None] | None = None
    #: (object_key) -> 文件是否可解码。缺省时退化为看记录里的布尔标记。
    file_probe: Callable[[str], bool] | None = None
    #: CUSTOM 检查的具名谓词表。
    predicates: MutableMapping[str, Callable[[Any, Mapping[str, Any], CheckContext], bool]] = field(
        default_factory=dict
    )
    #: 自由扩展位，供调用方塞业务上下文。
    extras: MutableMapping[str, Any] = field(default_factory=dict)

    def effective_server_time(self) -> datetime:
        return self.server_time or self.now


# --------------------------------------------------------------------------- 取值


def extract(record: Mapping[str, Any], path: str | None) -> Any:
    """按点分路径从记录里取值；缺失返回 :data:`MISSING`。

    ``path`` 为 None 时返回整条记录——REQUIRED_FLAGS 这类「看多个字段」的检查用。

    >>> extract({"a": {"b": 1}}, "a.b")
    1
    >>> extract({"a": 1}, "x") is MISSING
    True
    """
    if path is None:
        return record
    cur: Any = record
    for seg in path.split("."):
        if isinstance(cur, Mapping) and seg in cur:
            cur = cur[seg]
        else:
            return MISSING
    return cur


def _is_blank(value: Any, *, allow_empty_string: bool = False) -> bool:
    if value is MISSING or value is None:
        return True
    if isinstance(value, str) and not allow_empty_string and value.strip() == "":
        return True
    if isinstance(value, float) and math.isnan(value):  # noqa: SIM103 - 统一 guard 链的末环，展平会破坏与上方同构分支的对称性
        return True
    return False


def _as_float(value: Any) -> float | None:
    """尽力转 float；转不了返回 None（由调用方决定这算不算失败）。"""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y%m%d%H%M%S",
)


def _as_datetime(value: Any, *, epoch_unit: str = "auto") -> datetime | None:
    """把常见的时间表达转成 datetime。

    支持 datetime / ISO 字符串 / yyyyMMddHHmmss / 秒级或毫秒级 epoch。
    epoch_unit 取 ``s`` / ``ms`` / ``auto``（按量级猜，> 1e11 视为毫秒）。
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = float(value)
        if epoch_unit == "ms" or (epoch_unit == "auto" and abs(num) > 1e11):
            num /= 1000.0
        try:
            return datetime.fromtimestamp(num)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
        for fmt in _TS_FORMATS:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        num = _as_float(raw)
        if num is not None:
            return _as_datetime(num, epoch_unit=epoch_unit)
    return None


# --------------------------------------------------------------------------- 检查实现

CheckFn = Callable[[Any, Mapping[str, Any], CheckContext], CheckOutcome]

#: 检查类型 → 实现。派发靠查表，禁止在业务代码里写 if check == ...
CHECKS: dict[CheckType, CheckFn] = {}


def register_check(check: CheckType) -> Callable[[CheckFn], CheckFn]:
    """注册一个检查实现。可用于在项目外扩展自定义检查类型。"""

    def deco(fn: CheckFn) -> CheckFn:
        if check in CHECKS:
            raise ValueError(f"检查类型 {check.value} 已注册: {CHECKS[check]!r}")
        CHECKS[check] = fn
        return fn

    return deco


def _skip_if_absent(value: Any, params: Mapping[str, Any]) -> bool:
    """可选字段缺失时是否跳过。默认 False——必填与否由 NOT_NULL 规则单独表达。"""
    return bool(params.get("allow_null", False)) and _is_blank(value, allow_empty_string=True)


@register_check(CheckType.NOT_NULL)
def _check_not_null(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """完整性：必填字段非空。空串默认算空，可用 allow_empty_string 放宽。"""
    allow_empty = bool(params.get("allow_empty_string", False))
    if _is_blank(value, allow_empty_string=allow_empty):
        return CheckOutcome.failed("必填字段为空", None if value is MISSING else value)
    return CheckOutcome.passed(value)


@register_check(CheckType.REGEX)
def _check_regex(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """有效性：格式符合规范。data_id 的三级 ID 正则就走这条。"""
    pattern = params.get("pattern")
    if not pattern:
        return CheckOutcome.failed("规则配置缺少 pattern 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    if _is_blank(value, allow_empty_string=True):
        return CheckOutcome.failed(
            "字段为空，无法做格式校验", value if value is not MISSING else None
        )
    text = str(value)
    if re.fullmatch(pattern, text) is None:
        return CheckOutcome.failed(f"不匹配格式 {pattern}", text)
    return CheckOutcome.passed(text)


@register_check(CheckType.ENUM)
def _check_enum(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """有效性：枚举值在字典内。"""
    allowed: Sequence[Any] = params.get("values") or ()
    if not allowed:
        return CheckOutcome.failed("规则配置缺少 values 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    if value is MISSING:
        return CheckOutcome.failed("字段缺失，无法做枚举校验")
    candidates = set(allowed)
    if bool(params.get("case_insensitive", False)) and isinstance(value, str):
        candidates = {str(a).lower() for a in allowed}
        probe: Any = value.lower()
    else:
        probe = value
    if probe not in candidates:
        return CheckOutcome.failed(f"取值不在字典 {sorted(map(str, allowed))} 内", value)
    return CheckOutcome.passed(value)


@register_check(CheckType.RANGE)
def _check_range(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """准确性：数值范围合法。min / max 均为闭区间，可只给一侧。"""
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    num = _as_float(value)
    if num is None:
        return CheckOutcome.failed("不是可比较的数值", value if value is not MISSING else None)
    lo = params.get("min")
    hi = params.get("max")
    if lo is not None and num < float(lo):
        return CheckOutcome.failed(f"小于下限 {lo}", num)
    if hi is not None and num > float(hi):
        return CheckOutcome.failed(f"大于上限 {hi}", num)
    return CheckOutcome.passed(num)


@register_check(CheckType.ABS_MAX)
def _check_abs_max(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """绝对值上限：时间同步误差 ≤ ±10ms（采集车）/ ±50ms（量产车）。"""
    limit = params.get("max_abs")
    if limit is None:
        return CheckOutcome.failed("规则配置缺少 max_abs 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    num = _as_float(value)
    if num is None:
        return CheckOutcome.failed("不是可比较的数值", value if value is not MISSING else None)
    if abs(num) > float(limit):
        return CheckOutcome.failed(f"绝对值 {abs(num)} 超过 ±{limit}", num)
    return CheckOutcome.passed(num)


@register_check(CheckType.RATIO_MAX)
def _check_ratio_max(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """比率上限：连续丢帧率 > 1% 升级告警、重复率 > 5% 告警。

    严格大于才算命中——原文写的是「> 5%」「> 1%」，等于阈值不告警。
    """
    limit = params.get("max_ratio")
    if limit is None:
        return CheckOutcome.failed("规则配置缺少 max_ratio 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    num = _as_float(value)
    if num is None:
        return CheckOutcome.failed("不是可比较的比率", value if value is not MISSING else None)
    if num > float(limit):
        return CheckOutcome.failed(f"比率 {num:.4f} 超过阈值 {limit}", num)
    return CheckOutcome.passed(num)


@register_check(CheckType.MIN_VALUE)
def _check_min_value(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """下限检查：回传片段覆盖触发前 ≥ N 秒 / 后 ≥ M 秒。"""
    limit = params.get("min")
    if limit is None:
        return CheckOutcome.failed("规则配置缺少 min 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    num = _as_float(value)
    if num is None:
        return CheckOutcome.failed("不是可比较的数值", value if value is not MISSING else None)
    if num < float(limit):
        return CheckOutcome.failed(f"{num} 小于要求的下限 {limit}", num)
    return CheckOutcome.passed(num)


@register_check(CheckType.TIMESTAMP_WINDOW)
def _check_timestamp_window(
    value: Any, params: Mapping[str, Any], ctx: CheckContext
) -> CheckOutcome:
    """准确性：时间戳不早于车辆出厂时间、不晚于服务器时间 + 容忍窗口。

    容忍窗口是为了容忍车端时钟漂移（原文 4.2），默认值见
    thresholds.CLOCK_DRIFT_TOLERANCE_SECONDS。
    """
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    moment = _as_datetime(value, epoch_unit=str(params.get("epoch_unit", "auto")))
    if moment is None:
        return CheckOutcome.failed("无法解析为时间戳", value if value is not MISSING else None)

    lower: datetime | None = None
    lower_field = params.get("not_before_field")
    if lower_field:
        lower = _as_datetime(extract(ctx.record, str(lower_field)))
    if lower is None and params.get("not_before"):
        lower = _as_datetime(params["not_before"])
    if lower is not None and moment < lower:
        return CheckOutcome.failed(f"早于下界 {lower.isoformat()}", moment.isoformat())

    tolerance = _as_float(params.get("future_tolerance_seconds", 0.0)) or 0.0
    upper = ctx.effective_server_time() + timedelta(seconds=tolerance)
    if moment > upper:
        return CheckOutcome.failed(
            f"晚于服务器时间 + 容忍窗口 {tolerance}s（上界 {upper.isoformat()}）",
            moment.isoformat(),
        )
    return CheckOutcome.passed(moment.isoformat())


@register_check(CheckType.REQUIRED_FLAGS)
def _check_required_flags(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """合规：一组标记必须同时为真（车端脱敏 + 合规云脱密 双合规标记）。

    真值判定：布尔 True、数值非 0、字符串在 truthy 集合内。
    """
    flags: Sequence[str] = params.get("flags") or ()
    if not flags:
        return CheckOutcome.failed("规则配置缺少 flags 参数")
    truthy = {
        str(x).lower() for x in params.get("truthy", ["true", "1", "y", "yes", "done", "passed"])
    }
    missing: list[str] = []
    for flag in flags:
        raw = extract(ctx.record, str(flag))
        if raw is MISSING or raw is None:
            missing.append(f"{flag}=<缺失>")
            continue
        if isinstance(raw, bool):
            ok = raw
        elif isinstance(raw, (int, float)):
            ok = raw != 0
        else:
            ok = str(raw).strip().lower() in truthy
        if not ok:
            missing.append(f"{flag}={raw!r}")
    if missing:
        return CheckOutcome.failed("合规标记缺失或未置位: " + ", ".join(missing), missing)
    return CheckOutcome.passed(list(flags))


@register_check(CheckType.REQUIRED_MEMBERS)
def _check_required_members(
    value: Any, params: Mapping[str, Any], ctx: CheckContext
) -> CheckOutcome:
    """完整性：集合覆盖。同一帧组内相机/激光雷达/毫米波/IMU/GNSS 文件齐全。"""
    required: Sequence[str] = params.get("members") or ()
    if not required:
        return CheckOutcome.failed("规则配置缺少 members 参数")
    if value is MISSING or value is None:
        if params.get("allow_null", False):
            return CheckOutcome.passed(None)
        return CheckOutcome.failed("帧组成员字段缺失", None)
    if isinstance(value, Mapping):
        present = {str(k) for k, v in value.items() if v}
    elif isinstance(value, str):
        present = {p.strip() for p in value.split(",") if p.strip()}
    elif isinstance(value, Iterable):
        present = {str(v) for v in value}
    else:
        return CheckOutcome.failed("帧组成员字段不是集合类型", value)
    lack = [m for m in required if str(m) not in present]
    if lack:
        return CheckOutcome.failed("缺少模态: " + ", ".join(map(str, lack)), sorted(present))
    return CheckOutcome.passed(sorted(present))


@register_check(CheckType.REFERENCE_EXISTS)
def _check_reference_exists(
    value: Any, params: Mapping[str, Any], ctx: CheckContext
) -> CheckOutcome:
    """一致性：跨源关联存在性。

    依赖 ctx.reference_lookup；未注入时视为通过并在 detail 里说明——
    旁路依赖不可用不能卡死主链路。
    """
    target_table = params.get("target_table")
    target_field = params.get("target_field")
    if not target_table or not target_field:
        return CheckOutcome.failed("规则配置缺少 target_table / target_field 参数")
    if _skip_if_absent(value, params) or _is_blank(value):
        if params.get("allow_null", False):
            return CheckOutcome.passed(value)
        return CheckOutcome.failed("关联键为空", None if value is MISSING else value)
    if ctx.reference_lookup is None:
        return CheckOutcome.passed(value)
    try:
        exists = bool(ctx.reference_lookup(str(target_table), str(target_field), value))
    except Exception as exc:  # 旁路依赖故障降级为通过，但把原因带出来
        return CheckOutcome(True, f"关联查询失败已降级放行: {exc}", value)
    if not exists:
        return CheckOutcome.failed(f"在 {target_table}.{target_field} 中找不到关联记录", value)
    return CheckOutcome.passed(value)


@register_check(CheckType.STATUS_TRANSITION)
def _check_status_transition(
    value: Any, params: Mapping[str, Any], ctx: CheckContext
) -> CheckOutcome:
    """有效性：状态流转合法。transitions 是 {旧状态: [允许的新状态]}。"""
    transitions: Mapping[str, Sequence[str]] = params.get("transitions") or {}
    from_field = params.get("from_field")
    if not transitions or not from_field:
        return CheckOutcome.failed("规则配置缺少 transitions / from_field 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    old = extract(ctx.record, str(from_field))
    if old is MISSING or old is None:
        return CheckOutcome.passed(value)  # 首次写入没有旧状态
    allowed = transitions.get(str(old))
    if allowed is None:
        return CheckOutcome.failed(f"未定义的源状态 {old!r}", [old, value])
    if str(value) not in {str(a) for a in allowed}:
        return CheckOutcome.failed(
            f"非法流转 {old!r} → {value!r}，允许 {list(allowed)}", [old, value]
        )
    return CheckOutcome.passed([old, value])


@register_check(CheckType.SCHEMA_PARSABLE)
def _check_schema_parsable(
    value: Any, params: Mapping[str, Any], ctx: CheckContext
) -> CheckOutcome:
    """有效性：Schema 可解析（P0 合规级典型场景之一）。

    value 为 JSON 字符串或 Mapping；required_fields 给出必须出现的键。
    """
    required: Sequence[str] = params.get("required_fields") or ()
    payload: Any = value
    if payload is MISSING or payload is None:
        if params.get("allow_null", False):
            return CheckOutcome.passed(None)
        return CheckOutcome.failed("报文缺失，无法解析 Schema")
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode(str(params.get("encoding", "utf-8")))
        except UnicodeDecodeError as exc:
            return CheckOutcome.failed(f"报文无法按 {params.get('encoding', 'utf-8')} 解码: {exc}")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError) as exc:
            return CheckOutcome.failed(f"Schema 不可解析: {exc}")
    if not isinstance(payload, Mapping):
        return CheckOutcome.failed("解析结果不是对象，Schema 不可解析", type(payload).__name__)
    lack = [f for f in required if extract(payload, str(f)) is MISSING]
    if lack:
        return CheckOutcome.failed("Schema 缺少必需字段: " + ", ".join(map(str, lack)), lack)
    return CheckOutcome.passed(sorted(payload.keys()))


@register_check(CheckType.DECODABLE)
def _check_decodable(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """有效性：文件可解码 / 校验和一致（压缩损伤、文件损坏）。

    优先用 ctx.file_probe 真探一次；没有探针时退化为比对记录里的 checksum。
    """
    if _is_blank(value):
        return CheckOutcome.failed("文件 key 为空", None if value is MISSING else value)
    object_key = str(value)
    if ctx.file_probe is not None:
        try:
            if not ctx.file_probe(object_key):
                return CheckOutcome.failed("文件探针判定不可解码", object_key)
            return CheckOutcome.passed(object_key)
        except Exception as exc:
            return CheckOutcome.failed(f"文件探针执行失败: {exc}", object_key)

    checksum_field = params.get("checksum_field")
    payload_field = params.get("payload_field")
    if checksum_field and payload_field:
        expected = extract(ctx.record, str(checksum_field))
        payload = extract(ctx.record, str(payload_field))
        if expected is MISSING or payload is MISSING:
            return CheckOutcome.failed("缺少校验和或报文本体，无法验证文件完整性", object_key)
        raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        algo = str(params.get("algorithm", "md5"))
        digest = hashlib.new(algo, raw).hexdigest()
        if digest.lower() != str(expected).lower():
            return CheckOutcome.failed(
                f"{algo} 校验和不一致: 期望 {expected}，实际 {digest}", digest
            )
        return CheckOutcome.passed(digest)

    flag_field = params.get("flag_field")
    if flag_field:
        flag = extract(ctx.record, str(flag_field))
        if flag is MISSING or flag is None:
            return CheckOutcome.failed("缺少可解码标记", object_key)
        ok = (
            flag
            if isinstance(flag, bool)
            else str(flag).strip().lower() in {"true", "1", "y", "yes", "ok"}
        )
        if not ok:
            return CheckOutcome.failed("记录标记该文件不可解码", flag)
    return CheckOutcome.passed(object_key)


@register_check(CheckType.UNIQUE_KEY)
def _check_unique_key(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """唯一性：主键 / 事件 ID 不重复。

    命中只代表「看见重复」；物理去重由 Paimon 主键 Upsert 幂等保证（原文 4.2），
    因此这条规则的 severity 通常是 WARNING 而不是 ERROR。
    """
    if _is_blank(value):
        if params.get("allow_null", False):
            # 空主键由同字段的 NOT_NULL 规则负责，这里不重复命中
            return CheckOutcome.passed(None)
        return CheckOutcome.failed("唯一键为空", None if value is MISSING else value)
    key_fields: Sequence[str] = params.get("key_fields") or ()
    if key_fields:
        parts = [str(extract(ctx.record, str(f))) for f in key_fields]
        key = "|".join(parts)
    else:
        key = str(value)
    scope_table = str(params.get("scope_table") or ctx.table)
    if ctx.duplicates.observe(scope_table, key):
        return CheckOutcome.failed(f"唯一键重复: {key}", key)
    return CheckOutcome.passed(key)


@register_check(CheckType.SIMILARITY_MAX)
def _check_similarity_max(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """唯一性：近重复场景标记（L2 近重复抑制 / L5 近重复）。

    value 是上游算出的相似度（0~1）。门禁只做阈值判定，不在这里算特征相似度——
    算法评分属于第二道防线。
    """
    limit = params.get("max_similarity")
    if limit is None:
        return CheckOutcome.failed("规则配置缺少 max_similarity 参数")
    if _skip_if_absent(value, params) or value is MISSING:
        return CheckOutcome.passed(value)
    num = _as_float(value)
    if num is None:
        return CheckOutcome.failed("相似度不是数值", value)
    if num > float(limit):
        return CheckOutcome.failed(f"相似度 {num} 超过近重复阈值 {limit}", num)
    return CheckOutcome.passed(num)


@register_check(CheckType.DISJOINT_SPLIT)
def _check_disjoint_split(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """L5 数据集划分防泄漏：训练/验证/测试集按 clip 级划分，相邻帧不得跨集。

    value 是本条记录声明的划分（train/val/test），group_field 指向 clip 级
    分组键（默认 data_id）。同一个 clip 已登记为别的划分即判定跨集泄漏。
    """
    group_field = str(params.get("group_field", "data_id"))
    group = extract(ctx.record, group_field)
    if _is_blank(group):
        return CheckOutcome.failed(f"缺少 clip 级分组键 {group_field}", None)
    if _is_blank(value):
        return CheckOutcome.failed("缺少数据集划分标记", None if value is MISSING else value)
    known: str | None = None
    if ctx.split_lookup is not None:
        try:
            known = ctx.split_lookup(str(group))
        except Exception as exc:
            return CheckOutcome(True, f"划分查询失败已降级放行: {exc}", value)
    else:
        registry: MutableMapping[str, str] = ctx.extras.setdefault("_split_registry", {})
        known = registry.get(str(group))
        if known is None:
            registry[str(group)] = str(value)
    if known is not None and str(known) != str(value):
        return CheckOutcome.failed(
            f"clip {group} 已属于 {known} 集，本条声明 {value}，跨集泄漏", [group, known, value]
        )
    return CheckOutcome.passed(value)


@register_check(CheckType.FRESHNESS)
def _check_freshness(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """及时性：入湖延迟 / CDC 同步延迟。原文定为「监控告警（不拦截）」。"""
    limit = _as_float(params.get("max_delay_seconds"))
    if limit is None:
        return CheckOutcome.failed("规则配置缺少 max_delay_seconds 参数")
    if _skip_if_absent(value, params):
        return CheckOutcome.passed(value)
    moment = _as_datetime(value, epoch_unit=str(params.get("epoch_unit", "auto")))
    if moment is None:
        return CheckOutcome.failed("无法解析为时间戳", value if value is not MISSING else None)
    delay = (ctx.effective_server_time() - moment).total_seconds()
    if delay > limit:
        return CheckOutcome.failed(f"延迟 {delay:.1f}s 超过阈值 {limit}s", delay)
    return CheckOutcome.passed(delay)


@register_check(CheckType.CUSTOM)
def _check_custom(value: Any, params: Mapping[str, Any], ctx: CheckContext) -> CheckOutcome:
    """逃生通道：调用 ctx.predicates 里注册的具名谓词。

    留这个口子是为了让「规则即数据」不至于被个别古怪场景破功；
    但凡能用声明式类型表达的，都不该走这里。
    """
    name = params.get("name")
    if not name:
        return CheckOutcome.failed("规则配置缺少 name 参数")
    fn = ctx.predicates.get(str(name))
    if fn is None:
        return CheckOutcome(True, f"未注册的自定义谓词 {name}，已跳过", value)
    try:
        ok = bool(fn(value, params, ctx))
    except Exception as exc:
        return CheckOutcome.failed(f"自定义谓词 {name} 执行失败: {exc}", value)
    if not ok:
        return CheckOutcome.failed(f"自定义谓词 {name} 判定不通过", value)
    return CheckOutcome.passed(value)


# --------------------------------------------------------------------------- 规则


def when_matches(record: Mapping[str, Any], when: Mapping[str, Any] | None) -> bool:
    """声明式前置条件：规则只在满足 ``when`` 时才执行。

    支持的算子（互相之间是「与」关系）::

        {"field": "annotation_source", "in": ["auto_label", "pretrain_model"]}
        {"field": "vehicle_type", "equals": "collect"}
        {"field": "status", "not_in": ["deleted"]}
        {"field": "qc_result_id", "exists": true}

    有了它，「预标注结果未经人工审核不得入训练数据集」这种带前提的规则
    仍然是一条声明，而不是代码里的 if。
    """
    if not when:
        return True
    path = when.get("field")
    if not path:
        raise ValueError(f"when 条件缺少 field: {dict(when)!r}")
    value = extract(record, str(path))
    if "exists" in when:
        exists = value is not MISSING and value is not None
        if exists is not bool(when["exists"]):
            return False
    if "equals" in when and str(value) != str(when["equals"]):
        return False
    if "not_equals" in when and str(value) == str(when["not_equals"]):
        return False
    if "in" in when and str(value) not in {str(v) for v in when["in"]}:
        return False
    if "not_in" in when and str(value) in {str(v) for v in when["not_in"]}:  # noqa: SIM103 - 统一 guard 链的末环，展平会破坏与上方同构分支的对称性
        return False
    return True


@dataclass(frozen=True, slots=True, eq=False)
class RuleSpec:
    """一条门禁规则的完整声明。

    YAML 三级组织「检查类型 → 表 → 字段」在这里体现为 (check, table, field) 三元组；
    其余字段回答的是原文第二章那句话——「查出来怎么办」。

    约束（__post_init__ 强制）：
      · P0 合规/安全级必须是 ERROR 硬拦截，不允许配成 WARNING 软告警；
      · 规则 ID 全局唯一且不为空（异常隔离表要靠它追责、复验要靠它重放）。
    """

    rule_id: str
    table: str
    check: CheckType
    severity: Severity
    dimension: QualityDimension
    issue_level: IssueLevel
    field: str | None = None
    params: Mapping[str, Any] = dc_field(default_factory=dict)
    #: 前置条件，见 :func:`when_matches`。空表示无条件执行。
    when: Mapping[str, Any] = dc_field(default_factory=dict)
    channel: Channel = Channel.COMMON
    quality_layer: QualityLayer | None = None
    scope: RuleScope = RuleScope.RECORD
    message: str = ""
    repair_action: RepairAction = RepairAction.MANUAL_REPAIR
    owner: str = ""
    enabled: bool = True
    #: 原文出处（章节 / 表格），便于审计每条规则的来历
    source: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.rule_id or not self.rule_id.strip():
            raise ValueError("rule_id 不能为空——隔离表要靠它追责、复验要靠它重放")
        if not self.table:
            raise ValueError(f"规则 {self.rule_id} 未声明作用表（'*' 表示全表通用）")
        if self.issue_level is IssueLevel.P0 and self.severity is not Severity.ERROR:
            raise ValueError(
                f"规则 {self.rule_id}: P0 为合规/安全级，必须是 ERROR 硬拦截，"
                f"不允许降级为 {self.severity.value} 软告警"
            )
        if self.check not in CHECKS:
            raise ValueError(f"规则 {self.rule_id}: 未注册的检查类型 {self.check!r}")
        if self.check is not CheckType.REQUIRED_FLAGS and self.scope is RuleScope.RECORD:
            needs_field = {
                CheckType.NOT_NULL,
                CheckType.REGEX,
                CheckType.ENUM,
                CheckType.RANGE,
                CheckType.ABS_MAX,
                CheckType.MIN_VALUE,
                CheckType.TIMESTAMP_WINDOW,
                CheckType.UNIQUE_KEY,
                CheckType.DECODABLE,
                CheckType.FRESHNESS,
            }
            if self.check in needs_field and not self.field:
                raise ValueError(f"规则 {self.rule_id}: {self.check.value} 必须声明 field")

    # ---- 判定 ----

    @property
    def disposition_hint(self) -> str:
        """这条规则命中后的处置（由 severity 决定，仅用于展示）。"""
        from .severity import DISPOSITION_BY_SEVERITY

        return DISPOSITION_BY_SEVERITY[self.severity].value

    @property
    def is_hard_block(self) -> bool:
        """是否硬拦截（ERROR → REJECT）。P0 恒为 True。"""
        return self.severity is Severity.ERROR

    def applies_to(self, table: str, channel: Channel | None = None) -> bool:
        """该规则是否作用于这张表 / 这个通道。``table='*'`` 表示全表通用。"""
        if self.table != "*" and self.table != table:
            return False
        if (  # noqa: SIM103 - 统一 guard 链的末环，展平会破坏与上方同构分支的对称性
            channel is not None
            and self.channel is not Channel.COMMON
            and self.channel is not channel
        ):
            return False
        return True

    def evaluate(self, record: Mapping[str, Any], ctx: CheckContext) -> RuleHit | None:
        """执行检查；通过返回 None，命中返回 :class:`RuleHit`。

        检查实现抛出的任何异常都被兜成一条命中（detail 带异常信息），
        绝不让单条坏数据把整个门禁进程打挂。
        """
        source: Mapping[str, Any] = ctx.batch_stats if self.scope is RuleScope.BATCH else record
        try:
            if not when_matches(record, self.when):
                return None
        except ValueError as exc:
            raise ValueError(f"规则 {self.rule_id} 的 when 条件非法: {exc}") from exc
        value = extract(source, self.field)
        fn = CHECKS[self.check]
        try:
            outcome = fn(value, self.params, ctx)
        except Exception as exc:  # 兜底：检查器自身异常 = 规则命中，可审计
            outcome = CheckOutcome.failed(f"检查器执行异常: {type(exc).__name__}: {exc}", None)
        if outcome.ok:
            return None
        return RuleHit(
            rule_id=self.rule_id,
            table=self.table,
            field=self.field,
            check=self.check,
            severity=self.severity,
            dimension=self.dimension,
            issue_level=self.issue_level,
            quality_layer=self.quality_layer,
            channel=self.channel,
            repair_action=self.repair_action,
            message=self.message or f"{self.check.value} 检查未通过",
            detail=outcome.detail,
            observed=outcome.observed,
            owner=self.owner,
        )

    # ---- 序列化（YAML 往返）----

    def to_dict(self) -> dict[str, Any]:
        """转成 YAML 友好的普通字典（不含 table/check——它们是 YAML 的前两级键）。"""
        out: dict[str, Any] = {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "dimension": self.dimension.key,
            "issue_level": self.issue_level.value,
            "channel": self.channel.value,
            "scope": self.scope.value,
            "repair_action": self.repair_action.value,
            "enabled": self.enabled,
        }
        if self.field:
            out["field"] = self.field
        if self.params:
            out["params"] = dict(self.params)
        if self.when:
            out["when"] = dict(self.when)
        if self.quality_layer is not None:
            out["quality_layer"] = self.quality_layer.key
        for key in ("message", "owner", "source", "notes"):
            val = getattr(self, key)
            if val:
                out[key] = val
        return out

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, table: str | None = None, check: str | None = None
    ) -> RuleSpec:
        """从 YAML 字典还原。table / check 可由 YAML 的层级键带入。"""
        table_name = str(data.get("table") or table or "")
        check_name = str(data.get("check") or check or "")
        if not check_name:
            raise ValueError(f"规则 {data.get('rule_id')!r} 缺少 check（检查类型）")
        layer = data.get("quality_layer")
        return cls(
            rule_id=str(data["rule_id"]),
            table=table_name,
            check=CheckType(check_name),
            severity=Severity(str(data.get("severity", "WARNING")).upper()),
            dimension=QualityDimension.by_key(str(data.get("dimension", "validity"))),
            issue_level=IssueLevel(str(data.get("issue_level", "P2")).upper()),
            field=data.get("field"),
            params=dict(data.get("params") or {}),
            when=dict(data.get("when") or {}),
            channel=Channel(str(data.get("channel", "common"))),
            quality_layer=QualityLayer.by_key(str(layer)) if layer else None,
            scope=RuleScope(str(data.get("scope", "record"))),
            message=str(data.get("message", "")),
            repair_action=RepairAction(str(data.get("repair_action", "B"))),
            owner=str(data.get("owner", "")),
            enabled=bool(data.get("enabled", True)),
            source=str(data.get("source", "")),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True, slots=True)
class RuleHit:
    """一次规则命中。隔离表 ods_quality_issue 的 hit 明细就是它的序列化。"""

    rule_id: str
    table: str
    field: str | None
    check: CheckType
    severity: Severity
    dimension: QualityDimension
    issue_level: IssueLevel
    quality_layer: QualityLayer | None
    channel: Channel
    repair_action: RepairAction
    message: str
    detail: str = ""
    observed: Any = None
    owner: str = ""
    #: 灰度期命中——只观测不处置（原文第六章「按表灰度发布新规则」）
    shadow: bool = False

    def as_shadow(self) -> RuleHit:
        """复制成灰度影子命中。"""
        return RuleHit(
            rule_id=self.rule_id,
            table=self.table,
            field=self.field,
            check=self.check,
            severity=self.severity,
            dimension=self.dimension,
            issue_level=self.issue_level,
            quality_layer=self.quality_layer,
            channel=self.channel,
            repair_action=self.repair_action,
            message=self.message,
            detail=self.detail,
            observed=self.observed,
            owner=self.owner,
            shadow=True,
        )

    def to_dict(self) -> dict[str, Any]:
        observed = self.observed
        if not isinstance(observed, (str, int, float, bool, type(None), list, dict)):
            observed = repr(observed)
        return {
            "rule_id": self.rule_id,
            "table": self.table,
            "field": self.field,
            "check": self.check.value,
            "severity": self.severity.value,
            "dimension": self.dimension.key,
            "issue_level": self.issue_level.value,
            "quality_layer": self.quality_layer.key if self.quality_layer else None,
            "channel": self.channel.value,
            "repair_action": self.repair_action.value,
            "message": self.message,
            "detail": self.detail,
            "observed": observed,
            "owner": self.owner,
            "shadow": self.shadow,
        }
