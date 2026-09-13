"""规则的声明式表达与全生命周期管理——「规则即数据」的 Python 侧模型。

原文第一设计决策（[S3-04] 一）：

    「规则即数据」：规则配置存在挖掘平台的 MySQL，经 Flink CDC 实时同步入湖
    （ods_mining_rule_config）。规则不是散落在代码里的 if-else，而是与业务数据
    一样可查询、可追溯、可审计的湖仓资产。

本模块提供的四件事，一一对应原文列的四项规则管理能力：

==============  ==========================================================
原文能力          本模块实现
==============  ==========================================================
双模式表达        :class:`ExpressionMode` + :class:`ConditionGroup`（可视化）
                 与 :class:`RawSqlCondition`（SQL），两条路编译产出等价 WHERE
全生命周期管理    :class:`RuleStatus` + :meth:`RuleDefinition.disable` 等，
                 每次变更产出一条 :class:`RuleChange` 留痕
优先级驱动下游    :class:`RulePriority` -> :class:`VectorizePolicy`
执行追溯          见 :mod:`adas_lakehouse.mining.executor`
==============  ==========================================================

条件类型覆盖原文列举的五类来源（[S3-04] 一）：「可组合标签、GPS 范围、时间、
传感器信号、模型输出等多类条件」，外加事件触发流（[S3-04] 二第五行）。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from enum import Enum
from typing import Any

from ..ids import content_hash
from ._sqlfmt import ident, join_predicates, literal, literal_list, qualified_ident
from .constants import (
    EVENT_WINDOW_AFTER_SEC,
    EVENT_WINDOW_BEFORE_SEC,
    HARSH_DECEL_MIN_DURATION_SEC,
    HARSH_DECEL_THRESHOLD_MPS2,
    RULE_TYPE_COUNT,
)

__all__ = [
    "Dialect",
    "ExecutionMode",
    "RuleType",
    "RuleStatus",
    "RulePriority",
    "VectorizePolicy",
    "ExpressionMode",
    "ConditionKind",
    "CompareOp",
    "TagMatch",
    "Condition",
    "TagCondition",
    "GeoFenceCondition",
    "TimeWindowCondition",
    "SignalCondition",
    "ModelOutputCondition",
    "EventCondition",
    "RawSqlCondition",
    "ConditionGroup",
    "condition_from_dict",
    "RuleDefinition",
    "RuleChange",
    "RuleValidationError",
    "SignalSample",
    "SustainedHit",
    "sustained_matches",
    "harsh_deceleration_condition",
    "rules_by_mode",
    "sort_by_priority",
    "SAMPLE_RULES",
]

#: 地球平均半径（米），Haversine 距离展开用。
#: ⚠️ 原文未明确，本项目设计：原文只写「GPS 围栏」，未给圆形围栏的距离算法。
EARTH_RADIUS_M = 6371000.0


class RuleValidationError(ValueError):
    """规则定义非法。入湖前就拦住，别让坏规则占着 4 小时批处理窗口。"""


# --------------------------------------------------------------------------- 枚举


class Dialect(str, Enum):
    """SQL 方言。

    原文技术选型（[S3-01] 五）：「批处理选 Spark on K8s（放弃 Hive，迭代开发与 UDF
    扩展性不足）；流处理选 Flink（Spark Streaming 事件准实时语义弱）」。
    所以批与流的编译目标是两个引擎，方言必须分开。
    """

    SPARK = "spark"
    FLINK = "flink"


class ExecutionMode(str, Enum):
    """执行模式。

    原文（[S3-04] 二）：「执行模式跟着条件来源走——静态标签与时空条件走 T+1 批，
    车辆信号与事件流走准实时，不是一刀切」。
    """

    #: T+1 批：Spark SQL 直接在 Paimon 表上执行（[S3-04] 三）
    BATCH_T_PLUS_1 = "batch_t_plus_1"
    #: 准实时流：Flink 消费触发事件流（[S3-04] 三）
    NEAR_REALTIME = "near_realtime"

    @property
    def dialect(self) -> Dialect:
        return Dialect.SPARK if self is ExecutionMode.BATCH_T_PLUS_1 else Dialect.FLINK

    @property
    def label_cn(self) -> str:
        return "T+1 批" if self is ExecutionMode.BATCH_T_PLUS_1 else "准实时"


@dataclass(frozen=True, slots=True)
class _RuleTypeSpec:
    """六大种类表格的一行，字段与原文表头一一对应。"""

    name_cn: str  # 「规则种类」列
    examples: tuple[str, ...]  # 「示例」列
    condition_note: str  # 「条件与执行」列，逐字抄原文
    modes: tuple[ExecutionMode, ...]  # 该种类允许的执行模式


class RuleType(str, Enum):
    """六大规则种类。逐行对应 [S3-04] 二的表格，示例与条件说明原文照抄。

    +--------------+---------------------+--------------------------------------------+
    | 规则种类     | 示例                | 条件与执行                                 |
    +==============+=====================+============================================+
    | 标签组合     | 雨天高速 / 夜间雾天 | 采集标签多字段 AND 组合，T+1 批            |
    | 时空地理     | 城市行人场景/通勤高峰| GPS 围栏 + 视角 + 时间段组合，T+1 批       |
    | 车辆信号     | 急减速 / 急变道     | CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时 |
    | 模型输出     | AEB 触发/行人险肇   | 模型信号直接引用，T+1 批或准实时           |
    | 事件触发     | 驾驶员接管          | 消费回传触发事件流，含前 15 后 5 秒窗口，准实时 |
    | 多条件复合   | 夜间雨天急刹        | 标签 + 信号多条件叠加，T+1 批              |
    +--------------+---------------------+--------------------------------------------+
    """

    TAG_COMBINATION = "tag_combination"
    SPATIOTEMPORAL = "spatiotemporal"
    VEHICLE_SIGNAL = "vehicle_signal"
    MODEL_OUTPUT = "model_output"
    EVENT_TRIGGER = "event_trigger"
    COMPOSITE = "composite"

    @property
    def spec(self) -> _RuleTypeSpec:
        return _RULE_TYPE_SPECS[self]

    @property
    def name_cn(self) -> str:
        return self.spec.name_cn

    @property
    def default_execution_mode(self) -> ExecutionMode:
        """默认执行模式 = 原文「条件与执行」列里排第一位的那个。"""
        return self.spec.modes[0]

    def allows(self, mode: ExecutionMode) -> bool:
        return mode in self.spec.modes


_RULE_TYPE_SPECS: dict[RuleType, _RuleTypeSpec] = {
    RuleType.TAG_COMBINATION: _RuleTypeSpec(
        "标签组合",
        ("雨天高速", "夜间雾天"),
        "采集标签多字段 AND 组合，T+1 批",
        (ExecutionMode.BATCH_T_PLUS_1,),
    ),
    RuleType.SPATIOTEMPORAL: _RuleTypeSpec(
        "时空地理",
        ("城市行人场景", "通勤高峰"),
        "GPS 围栏 + 视角 + 时间段组合，T+1 批",
        (ExecutionMode.BATCH_T_PLUS_1,),
    ),
    RuleType.VEHICLE_SIGNAL: _RuleTypeSpec(
        "车辆信号",
        ("急减速", "急变道"),
        "CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时",
        (ExecutionMode.NEAR_REALTIME,),
    ),
    RuleType.MODEL_OUTPUT: _RuleTypeSpec(
        "模型输出",
        ("AEB 触发", "行人险肇"),
        "模型信号直接引用，T+1 批或准实时",
        # 原文这一行明确写了「T+1 批或准实时」，是唯一双模式的种类
        (ExecutionMode.BATCH_T_PLUS_1, ExecutionMode.NEAR_REALTIME),
    ),
    RuleType.EVENT_TRIGGER: _RuleTypeSpec(
        "事件触发",
        ("驾驶员接管",),
        "消费回传触发事件流，含前 15 后 5 秒窗口，准实时",
        (ExecutionMode.NEAR_REALTIME,),
    ),
    RuleType.COMPOSITE: _RuleTypeSpec(
        "多条件复合",
        ("夜间雨天急刹",),
        "标签 + 信号多条件叠加，T+1 批",
        (ExecutionMode.BATCH_T_PLUS_1,),
    ),
}

assert len(_RULE_TYPE_SPECS) == RULE_TYPE_COUNT, "规则种类必须恰好六大种类（[S3-04] 二）"


class RuleStatus(str, Enum):
    """规则生命周期状态。

    原文（[S3-04] 一）：「全生命周期管理：创建 / 修改 / 禁用 / 优先级 / 版本，
    变更全程留痕」。原文给的是动作而非状态机，状态取值由本项目落地：

    ⚠️ 原文未明确，本项目设计：DRAFT/ENABLED/DISABLED/ARCHIVED 四态。
    只有 ENABLED 会被调度器选中执行；DISABLED 保留配置与历史命中，可随时启用；
    ARCHIVED 为不再启用的终态，仅供审计追溯。
    """

    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class VectorizePolicy(str, Enum):
    """向量化分级。

    原文（[S3-01] 五）：「向量化本身也分成本等级：高价值数据（规则命中 / 事件抽帧 /
    VLM 标签）优先向量化，普通数据抽样处理——GPU 成本花在刀刃上」。
    原文（[S3-04] 一）：「高优先级规则命中的数据优先进入向量化队列」。
    """

    #: 优先向量化，进 Embedding 优先队列
    PRIORITY = "priority_vectorize"
    #: 抽样处理
    SAMPLED = "sampled"


class RulePriority(str, Enum):
    """规则优先级。

    原文（[S3-04] 一）只说「rule_priority 不只是排序字段——它直接决定 Embedding 与
    存储分级，高优先级规则命中的数据优先进入向量化队列」，没有给出档位定义。

    ⚠️ 原文未明确，本项目设计：四档 P0/P1/P2/P3。P0/P1 视为原文口中的「高价值数据」，
    命中即优先向量化；P2/P3 为「普通数据」，走抽样处理（对齐 [S3-01] 五的成本分级）。
    档位同时作为 :mod:`adas_lakehouse.mining.scoring` 打分的先验权重。
    """

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def rank(self) -> int:
        """数值化档位，0 最高。排序与打分都用它。"""
        return int(self.value[1:])

    @property
    def vectorize_policy(self) -> VectorizePolicy:
        """优先级 → 向量化队列分级。⚠️ 分界线（P1 与 P2 之间）为本项目设计。"""
        return VectorizePolicy.PRIORITY if self.rank <= 1 else VectorizePolicy.SAMPLED

    @property
    def prior_score(self) -> float:
        """优先级先验分，映射到 [0, 1]。P0=1.0, P1≈0.67, P2≈0.33, P3=0.0。

        ⚠️ 原文未明确，本项目设计：线性等距映射，便于与其它打分因子加权求和。
        """
        return (len(RulePriority) - 1 - self.rank) / (len(RulePriority) - 1)


class ExpressionMode(str, Enum):
    """规则表达模式。

    原文（[S3-04] 一）：「双模式表达：SQL 条件 + 可视化配置——工程师写 SQL，
    业务同学拖配置，产出的规则等价」。「等价」是硬要求：两种模式最终都编译成
    同一个 WHERE 子句，由 :meth:`RuleDefinition.condition` 收口。
    """

    SQL = "sql"
    VISUAL = "visual"


class ConditionKind(str, Enum):
    """条件来源类型。

    原文（[S3-04] 一）：「可组合标签、GPS 范围、时间、传感器信号、模型输出等多类条件」。
    EVENT 一项来自 [S3-04] 二的「事件触发」行（消费回传触发事件流）。
    """

    TAG = "tag"  # 标签
    GEO = "geo"  # GPS 范围
    TIME = "time"  # 时间
    SIGNAL = "signal"  # 传感器信号
    MODEL_OUTPUT = "model"  # 模型输出
    EVENT = "event"  # 事件流
    RAW_SQL = "raw_sql"  # 工程师直接写的 SQL 条件
    GROUP = "group"  # 逻辑组合


class CompareOp(str, Enum):
    """比较运算符。"""

    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    EQ = "="
    NEQ = "<>"

    @property
    def sql(self) -> str:
        return self.value


#: 比较算子的 Python 实现。SQL 侧渲染成同名算子，两边同一套语义——
#: 尤其是 ``<``：原文「CAN 减速度 < -4m/s²」写的是严格小于，实测恰好 -4.0 不算命中。
_COMPARATORS: dict[CompareOp, Any] = {
    CompareOp.LT: lambda v, t: v < t,
    CompareOp.LTE: lambda v, t: v <= t,
    CompareOp.GT: lambda v, t: v > t,
    CompareOp.GTE: lambda v, t: v >= t,
    CompareOp.EQ: lambda v, t: v == t,
    CompareOp.NEQ: lambda v, t: v != t,
}


class TagMatch(str, Enum):
    """标签匹配语义。

    原文（[S3-04] 二）「标签组合」行写的是「采集标签多字段 AND 组合」——
    多个标签字段之间是 AND；单个字段内部多取值的语义（ANY/ALL）原文未提。

    ⚠️ 原文未明确，本项目设计：ANY = 取值命中其一；ALL = 多值字段需同时包含全部取值。
    """

    ANY = "any"
    ALL = "all"


# --------------------------------------------------------------------------- 条件模型


class Condition(ABC):
    """条件基类：可视化配置树的节点，也是 SQL 编译的输入。

    每个节点必须能独立渲染成一个 SQL 谓词，并声明自己依赖哪些列——
    依赖列用于编译期做「这条规则能不能在这张表上跑」的静态检查。
    """

    kind: ConditionKind

    @abstractmethod
    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        """渲染成 SQL 谓词（不含 WHERE 关键字）。"""

    @abstractmethod
    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        """本条件引用到的列名集合（不含别名前缀）。

        **必须与同方言下 :meth:`to_sql` 真正渲染出来的列一致。** 多报会把好规则
        误判成「引用了扫描计划没有的列」（strict 模式下直接拒编译），少报则让
        SQL 打到真实引擎上才报 column not found。唯一会让两者分叉的是
        :class:`SignalCondition`——它的持续时长判定在批方言下走 UDF、在流方言下走
        MATCH_RECOGNIZE，两条路读的列不是同一组，所以本方法带方言参数。
        """

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """序列化成可视化配置 JSON（落 ods_mining_rule_config.visual_config_json）。"""

    def describe(self) -> str:
        """人类可读描述，用于控制台展示与执行日志。"""
        return self.to_sql()

    @staticmethod
    def _col(name: str, alias: str) -> str:
        """渲染带别名的列引用。"""
        if "." in name:
            return qualified_ident(name)
        return f"{ident(alias)}.{ident(name)}" if alias else ident(name)


@dataclass(frozen=True, slots=True)
class TagCondition(Condition):
    """标签条件——原文「标签组合」种类的构件（雨天高速 / 夜间雾天）。

    Args:
        column: 标签所在列，如 ``weather`` / ``road_type`` / ``light_condition``
            （列名取自共享契约 dwd_collect_clip_detail）。
        values: 期望取值。经统一标签服务的字典映射后，这里存的是标准标签码。
        match: ANY 命中其一 / ALL 需全部包含（多值列）。
        multi_valued: 该列是否为 ARRAY 类型的多值标签列。
        negate: 取反（NOT IN）。
    """

    column: str
    values: tuple[Any, ...]
    match: TagMatch = TagMatch.ANY
    multi_valued: bool = False
    negate: bool = False
    kind: ConditionKind = field(default=ConditionKind.TAG, init=False)

    def __post_init__(self) -> None:
        if not self.values:
            raise RuleValidationError(f"标签条件 {self.column!r} 的取值列表不能为空")
        if self.match is TagMatch.ALL and not self.multi_valued:
            raise RuleValidationError(
                f"标签条件 {self.column!r} 用了 ALL 语义，但列不是多值列——"
                "单值列上 ALL 恒为假，多半是配置错了"
            )

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        col = self._col(self.column, alias)
        if self.multi_valued:
            # ARRAY_CONTAINS 在 Spark 与 Flink 上同名同义
            parts = [f"ARRAY_CONTAINS({col}, {literal(v)})" for v in self.values]
            op = "AND" if self.match is TagMatch.ALL else "OR"
            pred = join_predicates(parts, op)
        else:
            pred = f"{col} IN {literal_list(self.values)}"
        return f"NOT ({pred})" if self.negate else pred

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        return {self.column.split(".")[-1]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "column": self.column,
            "values": list(self.values),
            "match": self.match.value,
            "multi_valued": self.multi_valued,
            "negate": self.negate,
        }


@dataclass(frozen=True, slots=True)
class GeoFenceCondition(Condition):
    """GPS 围栏条件——原文「时空地理」种类的构件（城市行人场景 / 通勤高峰）。

    三种围栏形状：

    * ``bbox``   经纬度矩形，纯比较，无需 UDF，最省；
    * ``circle`` 圆形，用 Haversine 展开成纯三角函数表达式，同样无需 UDF；
    * ``polygon`` 任意多边形，落到自定义 UDF ——原文（[S3-04] 三）明确留了这条路：
      「复杂规则挂自定义 UDF，表达力不受限」。

    ⚠️ 原文未明确，本项目设计：三种形状的划分、Haversine 展开式、UDF 函数名
    ``mining_point_in_polygon`` 均为本项目落地方案；原文只写了「GPS 围栏」四个字。
    """

    shape: str = "bbox"
    lat_column: str = "gps_start_lat"
    lon_column: str = "gps_start_lon"
    min_lat: float | None = None
    max_lat: float | None = None
    min_lon: float | None = None
    max_lon: float | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    radius_m: float | None = None
    polygon: tuple[tuple[float, float], ...] = ()
    fence_name: str = ""
    kind: ConditionKind = field(default=ConditionKind.GEO, init=False)

    def __post_init__(self) -> None:
        if self.shape == "bbox":
            missing = [
                n
                for n, v in (
                    ("min_lat", self.min_lat),
                    ("max_lat", self.max_lat),
                    ("min_lon", self.min_lon),
                    ("max_lon", self.max_lon),
                )
                if v is None
            ]
            if missing:
                raise RuleValidationError(f"bbox 围栏缺少边界: {missing}")
            if self.min_lat >= self.max_lat or self.min_lon >= self.max_lon:  # type: ignore[operator]
                raise RuleValidationError("bbox 围栏的 min 必须小于 max")
        elif self.shape == "circle":
            if self.center_lat is None or self.center_lon is None or self.radius_m is None:
                raise RuleValidationError("circle 围栏需要 center_lat / center_lon / radius_m")
            if self.radius_m <= 0:
                raise RuleValidationError("circle 围栏半径必须为正")
        elif self.shape == "polygon":
            if len(self.polygon) < 3:
                raise RuleValidationError("polygon 围栏至少需要 3 个顶点")
        else:
            raise RuleValidationError(f"未知围栏形状: {self.shape!r}（只支持 bbox/circle/polygon）")

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        lat = self._col(self.lat_column, alias)
        lon = self._col(self.lon_column, alias)
        if self.shape == "bbox":
            return (
                f"({lat} BETWEEN {literal(self.min_lat)} AND {literal(self.max_lat)}"
                f" AND {lon} BETWEEN {literal(self.min_lon)} AND {literal(self.max_lon)})"
            )
        if self.shape == "circle":
            # Haversine：2R·asin(sqrt(sin²(Δφ/2) + cosφ1·cosφ2·sin²(Δλ/2)))
            clat, clon, r = (
                literal(self.center_lat),
                literal(self.center_lon),
                literal(self.radius_m),
            )
            er = literal(EARTH_RADIUS_M)
            return (
                f"(2 * {er} * ASIN(SQRT("
                f"POWER(SIN(RADIANS({lat} - {clat}) / 2), 2)"
                f" + COS(RADIANS({clat})) * COS(RADIANS({lat}))"
                f" * POWER(SIN(RADIANS({lon} - {clon}) / 2), 2)"
                f")) <= {r})"
            )
        # polygon：走自定义 UDF（[S3-04] 三「复杂规则挂自定义 UDF」）
        wkt = ", ".join(f"{lo} {la}" for la, lo in self.polygon)
        first_lat, first_lon = self.polygon[0]
        closed = f"POLYGON(({wkt}, {first_lon} {first_lat}))"
        return f"mining_point_in_polygon({lat}, {lon}, {literal(closed)})"

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        return {self.lat_column.split(".")[-1], self.lon_column.split(".")[-1]}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind.value,
            "shape": self.shape,
            "lat_column": self.lat_column,
            "lon_column": self.lon_column,
            "fence_name": self.fence_name,
        }
        if self.shape == "bbox":
            out.update(
                min_lat=self.min_lat,
                max_lat=self.max_lat,
                min_lon=self.min_lon,
                max_lon=self.max_lon,
            )
        elif self.shape == "circle":
            out.update(
                center_lat=self.center_lat, center_lon=self.center_lon, radius_m=self.radius_m
            )
        else:
            out["polygon"] = [list(p) for p in self.polygon]
        return out


@dataclass(frozen=True, slots=True)
class TimeWindowCondition(Condition):
    """时间条件——原文「时空地理」行里的「时间段组合」（通勤高峰）。

    Args:
        column: 时间列，默认 clip 采集开始时间。
        start_time / end_time: 一天内的时段。跨零点（如 22:00-06:00 夜间）自动按 OR 展开。
        weekdays: ISO 星期几集合（1=周一 … 7=周日），空表示不限。
        date_from / date_to: 日期范围，空表示不限。
    """

    column: str = "collect_start_time"
    start_time: time | None = None
    end_time: time | None = None
    weekdays: tuple[int, ...] = ()
    date_from: datetime | None = None
    date_to: datetime | None = None
    kind: ConditionKind = field(default=ConditionKind.TIME, init=False)

    def __post_init__(self) -> None:
        if (self.start_time is None) != (self.end_time is None):
            raise RuleValidationError("时段条件的 start_time / end_time 必须成对出现")
        for d in self.weekdays:
            if not 1 <= d <= 7:
                raise RuleValidationError(f"weekdays 取值需在 1..7（ISO），收到 {d}")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise RuleValidationError("date_from 不能晚于 date_to")
        if not any((self.start_time, self.weekdays, self.date_from, self.date_to)):
            raise RuleValidationError("时间条件至少要给出时段/星期/日期范围中的一项")

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        col = self._col(self.column, alias)
        parts: list[str] = []
        if self.start_time is not None and self.end_time is not None:
            hour = f"HOUR({col}) * 60 + MINUTE({col})"
            lo = self.start_time.hour * 60 + self.start_time.minute
            hi = self.end_time.hour * 60 + self.end_time.minute
            if lo <= hi:
                parts.append(f"({hour} BETWEEN {lo} AND {hi})")
            else:
                # 跨零点时段（夜间雾天、夜间雨天急刹这类规则会用到）
                parts.append(f"({hour} >= {lo} OR {hour} <= {hi})")
        if self.weekdays:
            # Spark/Flink 的 DAYOFWEEK 都是 1=周日，这里统一转成 ISO（1=周一）
            iso_dow = f"MOD(DAYOFWEEK({col}) + 5, 7) + 1"
            parts.append(f"({iso_dow} IN {literal_list(sorted(self.weekdays))})")
        if self.date_from is not None:
            parts.append(f"({col} >= {literal(self.date_from)})")
        if self.date_to is not None:
            parts.append(f"({col} <= {literal(self.date_to)})")
        return join_predicates(parts, "AND")

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        return {self.column.split(".")[-1]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "column": self.column,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "weekdays": list(self.weekdays),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
        }


@dataclass(frozen=True, slots=True)
class SignalCondition(Condition):
    """传感器/CAN 信号条件——原文「车辆信号」种类的构件（急减速 / 急变道）。

    原文给的唯一具体阈值就在这里（[S3-04] 二）：
    「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」。
    见 :func:`harsh_deceleration_condition`，它按原文数字直接构造好了这条规则。

    ``min_duration_sec`` 一旦给出，就不是一个简单的行级比较了，而是「持续时长」判定：

    * 批（Spark）：下推给自定义 UDF ``mining_signal_sustained``——原文（[S3-04] 三）
      为此明确留了口子：「复杂规则挂自定义 UDF，表达力不受限」；
    * 流（Flink）：由 :mod:`adas_lakehouse.mining.compiler` 生成 MATCH_RECOGNIZE 模式匹配，
      本方法在流方言下渲染的是单点比较，持续性交给 MATCH_RECOGNIZE 的 ``+`` 量词与
      窗口时长约束表达。

    ⚠️ 原文未明确，本项目设计：UDF 名 ``mining_signal_sustained``、
    信号列命名（``signal_name`` / ``signal_value``）、以及「批走 UDF、流走
    MATCH_RECOGNIZE」的分工，都是本项目的落地方案。
    """

    signal: str
    op: CompareOp
    threshold: float
    min_duration_sec: float | None = None
    value_column: str = "signal_value"
    name_column: str = "signal_name"
    #: 批模式下 UDF 的 clip 级入参列。UDF 拿 data_id 去取该 clip 的 CAN 时序，
    #: 于是「持续时长」判定不需要把 CAN 明细表 JOIN 进规则查询。
    subject_column: str = "data_id"
    kind: ConditionKind = field(default=ConditionKind.SIGNAL, init=False)

    #: 持续时长判定的自定义 UDF 名。⚠️ 原文未明确，本项目设计。
    UDF_NAME = "mining_signal_sustained"

    def __post_init__(self) -> None:
        if not self.signal:
            raise RuleValidationError("信号名不能为空")
        if self.min_duration_sec is not None and self.min_duration_sec <= 0:
            raise RuleValidationError("持续时长必须为正")

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        name_col = self._col(self.name_column, alias)
        val_col = self._col(self.value_column, alias)
        point = f"({name_col} = {literal(self.signal)} AND {val_col} {self.op.sql} {literal(self.threshold)})"
        if self.min_duration_sec is None or dialect is Dialect.FLINK:
            # 流方言：持续性由 MATCH_RECOGNIZE 表达，这里只给单点比较
            return point
        subject = self._col(self.subject_column, alias)
        return (
            f"{self.UDF_NAME}({subject}, "
            f"{literal(self.signal)}, {literal(self.op.sql)}, "
            f"{literal(self.threshold)}, {literal(self.min_duration_sec)})"
        )

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        """与 :meth:`to_sql` 在同一方言下真正渲染出来的列严格对齐。

        分方言的理由是 to_sql 本身就分方言：带 ``min_duration_sec`` 的条件在
        Spark 下渲染成 ``mining_signal_sustained(<subject>, …)``——**只读 subject 一列**，
        信号明细由 UDF 自己按 data_id 去取；在 Flink 下渲染成单点比较，读 name/value 两列，
        持续性交给 MATCH_RECOGNIZE。

        以前这里不分方言、一律返回三列的并集，后果很具体：原文「多条件复合」那条
        「夜间雨天急刹」（标签 + 信号叠加，T+1 批）在批扫描计划（只有 clip 基表）上
        必然多报 ``signal_name`` / ``signal_value`` 两列——非 strict 下每轮刷一条假告警，
        strict 下直接被判成不可编译。原文明明白白把这条规则列在六大种类表里，
        它不该编译不过。
        """
        name_value = {self.name_column.split(".")[-1], self.value_column.split(".")[-1]}
        if self.min_duration_sec is None:
            return name_value
        if dialect is Dialect.FLINK:
            return name_value
        return {self.subject_column.split(".")[-1]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "signal": self.signal,
            "op": self.op.value,
            "threshold": self.threshold,
            "min_duration_sec": self.min_duration_sec,
            "value_column": self.value_column,
            "name_column": self.name_column,
            "subject_column": self.subject_column,
        }

    def describe(self) -> str:
        dur = f" 持续 ≥ {self.min_duration_sec}s" if self.min_duration_sec is not None else ""
        return f"{self.signal} {self.op.sql} {self.threshold}{dur}"

    # ---- 求值：与编译出来的 SQL 同一套语义 ----

    def matches_point(self, value: float | None) -> bool:
        """单个采样点满不满足阈值条件。

        这是「CAN 减速度 < -4m/s²」里那个 ``<`` 的**唯一** Python 实现，
        :func:`sustained_matches` 与流式 MATCH_RECOGNIZE 的 ``DEFINE A`` 共用它的语义：

        * ``<`` 是**严格**小于——原文写的就是 ``<``，实测恰好 -4.0 不触发；
        * ``≥`` 的持续时长判定不在这里，见 :func:`sustained_matches`。

        Args:
            value: 实测信号值。``None``（该采样点没有值）一律判为不满足——
                缺值不是命中，宁可漏也不能凭空造一次命中。
        """
        if value is None:
            return False
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v != v:  # NaN：与任何阈值比较都是 False，显式挡掉免得被当成不满足以外的东西
            return False
        thr = float(self.threshold)
        return _COMPARATORS[self.op](v, thr)

    @property
    def peak_aggregate(self) -> str:
        """「最严重的那个采样点」该取 MIN 还是 MAX。

        严重度打分（:func:`~adas_lakehouse.mining.scoring.signal_severity`）看的是
        实测值离阈值多远，所以「峰值」必须往**超阈的那一侧**取：

        * ``<`` / ``<=``（急减速：减速度越负越严重）→ ``MIN``；
        * ``>`` / ``>=``（急变道：横向加速度越大越严重）→ ``MAX``。

        以前两边都写死 MIN，后果很具体：一条 ``lateral_accel > 4`` 的急变道规则
        会把整段里**最轻**的那个采样点当成峰值报上去，severity 恒为 0，
        高价值评分把最该细筛的那批命中排到最后。
        ``=`` / ``<>`` 两个算子上整段取值要么相等要么无所谓，沿用 MIN。
        """
        return "MAX" if self.op in (CompareOp.GT, CompareOp.GTE) else "MIN"


@dataclass(frozen=True, slots=True)
class ModelOutputCondition(Condition):
    """模型输出条件——原文「模型输出」种类的构件（AEB 触发 / 行人险肇）。

    原文对这一类的描述只有五个字：「模型信号直接引用」。所以这里就是一个直白的
    列比较，唯一额外做的事是可选地把模型版本（全链路公共键 model_version）带上，
    避免不同版本的模型输出被混在一条规则里。
    """

    output_column: str
    op: CompareOp = CompareOp.EQ
    value: Any = True
    model_version: str = ""
    model_version_column: str = "model_version"
    kind: ConditionKind = field(default=ConditionKind.MODEL_OUTPUT, init=False)

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        col = self._col(self.output_column, alias)
        pred = f"{col} {self.op.sql} {literal(self.value)}"
        if self.model_version:
            ver = self._col(self.model_version_column, alias)
            pred = f"({pred} AND {ver} = {literal(self.model_version)})"
        return pred

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        cols = {self.output_column.split(".")[-1]}
        if self.model_version:
            cols.add(self.model_version_column.split(".")[-1])
        return cols

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "output_column": self.output_column,
            "op": self.op.value,
            "value": self.value,
            "model_version": self.model_version,
            "model_version_column": self.model_version_column,
        }


@dataclass(frozen=True, slots=True)
class EventCondition(Condition):
    """事件触发条件——原文「事件触发」种类的构件（驾驶员接管）。

    原文（[S3-04] 二）：「消费回传触发事件流，含前 15 后 5 秒窗口，准实时」。
    窗口的两个数字写死在 :mod:`~adas_lakehouse.mining.constants`：
    ``EVENT_WINDOW_BEFORE_SEC = 15`` / ``EVENT_WINDOW_AFTER_SEC = 5``，
    本条件不允许改——改了就不是原文的规则了。窗口同时是补抽帧的加密采样区间
    （[S3-04] 三隐藏联动：「事件抽帧引擎立刻回头对前 15 后 5 秒窗口加密采样」）。
    """

    trigger_types: tuple[str, ...]
    type_column: str = "trigger_type"
    #: 事件时刻列。默认值是 registry 里 ods_vehicle_trigger_event 的真实列名
    #: ``trigger_time``——写成 event_time 的话，Flink 作业提交时才会报「column not found」。
    event_time_column: str = "trigger_time"
    kind: ConditionKind = field(default=ConditionKind.EVENT, init=False)

    #: 窗口固定 15/5，来自原文，不可配置
    window_before_sec: int = field(default=EVENT_WINDOW_BEFORE_SEC, init=False)
    window_after_sec: int = field(default=EVENT_WINDOW_AFTER_SEC, init=False)

    def __post_init__(self) -> None:
        if not self.trigger_types:
            raise RuleValidationError("事件条件至少要指定一个 trigger_type")

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        col = self._col(self.type_column, alias)
        return f"{col} IN {literal_list(self.trigger_types)}"

    def window_sql(self, *, alias: str = "") -> tuple[str, str]:
        """渲染事件窗口的起止表达式，供补抽帧与结果落表使用。

        Returns:
            ``(window_start_expr, window_end_expr)``，分别是 event_time 前 15 秒与后 5 秒。
        """
        col = self._col(self.event_time_column, alias)
        start = f"{col} - INTERVAL '{self.window_before_sec}' SECOND"
        end = f"{col} + INTERVAL '{self.window_after_sec}' SECOND"
        return start, end

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        return {self.type_column.split(".")[-1], self.event_time_column.split(".")[-1]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "trigger_types": list(self.trigger_types),
            "type_column": self.type_column,
            "event_time_column": self.event_time_column,
            "window_before_sec": self.window_before_sec,
            "window_after_sec": self.window_after_sec,
        }


#: SQL 条件里绝对不能出现的关键字——规则配置来自控制面 MySQL，虽然是内部系统，
#: 但「工程师写 SQL」这条路径天然是任意 SQL 注入点，编译期必须拦。
#: ⚠️ 原文未明确，本项目设计。
_FORBIDDEN_SQL = re.compile(
    # 关键字这一组后面跟 \b（防止误伤 `created_at` 这种以关键字开头的列名）；
    # `set x =` 与分隔符/注释**不能**跟 \b——`=` 后面往往是空格或引号，
    # 补一个 \b 会让这条分支永远匹配不上，等于这道闸形同虚设。
    r"(?is)\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|call|merge|"
    r"load\s+data)\b"
    r"|\bset\s+\w+\s*=|;|--|/\*"
)


@dataclass(frozen=True, slots=True)
class RawSqlCondition(Condition):
    """工程师手写的 SQL 条件——原文双模式表达里的 SQL 那一模式。

    原文（[S3-04] 一）：「工程师写 SQL，业务同学拖配置，产出的规则等价」。
    本类只做三件事：语法白名单校验（不含 DML/DDL、不含语句分隔符与注释）、
    加括号保证结合优先级、声明它引用了哪些列（用正则粗提，仅供告警）。
    """

    sql: str
    declared_columns: tuple[str, ...] = ()
    kind: ConditionKind = field(default=ConditionKind.RAW_SQL, init=False)

    def __post_init__(self) -> None:
        text = (self.sql or "").strip()
        if not text:
            raise RuleValidationError("SQL 条件不能为空")
        if _FORBIDDEN_SQL.search(text):
            raise RuleValidationError(
                f"SQL 条件命中禁用关键字/字符（DML、DDL、分号或注释）: {text[:80]!r}"
            )
        if text.count("(") != text.count(")"):
            raise RuleValidationError(f"SQL 条件括号不配对: {text[:80]!r}")

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        return f"({self.sql.strip()})"

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        if self.declared_columns:
            return {c.split(".")[-1] for c in self.declared_columns}
        return set(re.findall(r"\b[a-z_][a-z0-9_]{2,}\b", self.sql.lower()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "sql": self.sql,
            "declared_columns": list(self.declared_columns),
        }


@dataclass(frozen=True, slots=True)
class ConditionGroup(Condition):
    """逻辑组合节点——可视化配置拖出来的那棵树。

    原文「多条件复合」种类（夜间雨天急刹）就是一棵 AND 树：标签 + 信号多条件叠加。
    """

    operator: str = "AND"
    children: tuple[Condition, ...] = ()
    negate: bool = False
    kind: ConditionKind = field(default=ConditionKind.GROUP, init=False)

    def __post_init__(self) -> None:
        if self.operator.upper() not in ("AND", "OR"):
            raise RuleValidationError(f"逻辑运算符只能是 AND/OR，收到 {self.operator!r}")
        if not self.children:
            raise RuleValidationError("条件组不能为空")

    def to_sql(self, dialect: Dialect = Dialect.SPARK, *, alias: str = "") -> str:
        parts = [c.to_sql(dialect, alias=alias) for c in self.children]
        pred = join_predicates(parts, self.operator)
        return f"NOT ({pred})" if self.negate else pred

    def referenced_columns(self, dialect: Dialect = Dialect.SPARK) -> set[str]:
        """方言必须透传给每个子节点，否则复合规则里的信号条件又会按默认方言算。"""
        out: set[str] = set()
        for c in self.children:
            out |= c.referenced_columns(dialect)
        return out

    def walk(self) -> Iterable[Condition]:
        """深度优先遍历整棵条件树（含自身）。"""
        yield self
        for c in self.children:
            if isinstance(c, ConditionGroup):
                yield from c.walk()
            else:
                yield c

    def kinds(self) -> set[ConditionKind]:
        """树里出现过的所有叶子条件类型，用于推断执行模式。"""
        return {c.kind for c in self.walk() if c.kind is not ConditionKind.GROUP}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "operator": self.operator.upper(),
            "negate": self.negate,
            "children": [c.to_dict() for c in self.children],
        }


# --------------------------------------------------------------------------- 反序列化

_LEAF_BUILDERS = {
    ConditionKind.TAG: lambda d: TagCondition(
        column=d["column"],
        values=tuple(d["values"]),
        match=TagMatch(d.get("match", "any")),
        multi_valued=bool(d.get("multi_valued", False)),
        negate=bool(d.get("negate", False)),
    ),
    ConditionKind.GEO: lambda d: GeoFenceCondition(
        shape=d.get("shape", "bbox"),
        lat_column=d.get("lat_column", "gps_start_lat"),
        lon_column=d.get("lon_column", "gps_start_lon"),
        min_lat=d.get("min_lat"),
        max_lat=d.get("max_lat"),
        min_lon=d.get("min_lon"),
        max_lon=d.get("max_lon"),
        center_lat=d.get("center_lat"),
        center_lon=d.get("center_lon"),
        radius_m=d.get("radius_m"),
        polygon=tuple((float(p[0]), float(p[1])) for p in d.get("polygon", ())),
        fence_name=d.get("fence_name", ""),
    ),
    ConditionKind.TIME: lambda d: TimeWindowCondition(
        column=d.get("column", "collect_start_time"),
        start_time=time.fromisoformat(d["start_time"]) if d.get("start_time") else None,
        end_time=time.fromisoformat(d["end_time"]) if d.get("end_time") else None,
        weekdays=tuple(d.get("weekdays", ())),
        date_from=datetime.fromisoformat(d["date_from"]) if d.get("date_from") else None,
        date_to=datetime.fromisoformat(d["date_to"]) if d.get("date_to") else None,
    ),
    ConditionKind.SIGNAL: lambda d: SignalCondition(
        signal=d["signal"],
        op=CompareOp(d["op"]),
        threshold=float(d["threshold"]),
        min_duration_sec=(
            float(d["min_duration_sec"]) if d.get("min_duration_sec") is not None else None
        ),
        value_column=d.get("value_column", "signal_value"),
        name_column=d.get("name_column", "signal_name"),
        subject_column=d.get("subject_column", "data_id"),
    ),
    ConditionKind.MODEL_OUTPUT: lambda d: ModelOutputCondition(
        output_column=d["output_column"],
        op=CompareOp(d.get("op", "=")),
        value=d.get("value", True),
        model_version=d.get("model_version", ""),
        model_version_column=d.get("model_version_column", "model_version"),
    ),
    ConditionKind.EVENT: lambda d: EventCondition(
        trigger_types=tuple(d["trigger_types"]),
        type_column=d.get("type_column", "trigger_type"),
        event_time_column=d.get("event_time_column", "trigger_time"),
    ),
    ConditionKind.RAW_SQL: lambda d: RawSqlCondition(
        sql=d["sql"],
        declared_columns=tuple(d.get("declared_columns", ())),
    ),
}


def condition_from_dict(data: dict[str, Any]) -> Condition:
    """从可视化配置 JSON 还原条件树。

    Args:
        data: ``to_dict()`` 的产物，也就是 ods_mining_rule_config.visual_config_json 的内容。

    Raises:
        RuleValidationError: JSON 结构不合法或条件类型未知。
    """
    if not isinstance(data, dict) or "kind" not in data:
        raise RuleValidationError(f"可视化配置节点缺少 kind: {data!r}")
    try:
        kind = ConditionKind(data["kind"])
    except ValueError as exc:
        raise RuleValidationError(f"未知条件类型: {data['kind']!r}") from exc

    if kind is ConditionKind.GROUP:
        children = tuple(condition_from_dict(c) for c in data.get("children", ()))
        return ConditionGroup(
            operator=data.get("operator", "AND"),
            children=children,
            negate=bool(data.get("negate", False)),
        )
    try:
        return _LEAF_BUILDERS[kind](data)
    except RuleValidationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise RuleValidationError(f"条件 {kind.value} 的配置不合法: {exc}") from exc


# --------------------------------------------------------------------------- 规则定义


@dataclass(frozen=True, slots=True)
class RuleChange:
    """一条规则变更留痕。

    原文（[S3-04] 一）：「创建 / 修改 / 禁用 / 优先级 / 版本，变更全程留痕」——
    这就是那个「痕」。留痕对象随规则一起经 Flink CDC 入湖，
    于是「谁在什么时候改了什么规则，一查便知」。
    """

    rule_id: str
    action: str  # create / update / disable / enable / priority / version / archive
    from_version: int
    to_version: int
    changed_at: datetime
    changed_by: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "action": self.action,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changed_at": self.changed_at.isoformat(),
            "changed_by": self.changed_by,
            "detail": self.detail,
        }


_RULE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{2,63}$")


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """一条挖掘规则的完整声明——落 ods_mining_rule_config 的那一行。

    不可变：任何修改都通过 :meth:`with_changes` / :meth:`disable` 等方法产出新实例
    并附带一条 :class:`RuleChange`，从源头保证「变更全程留痕」。

    Attributes:
        rule_id: 规则 ID，命中结果携带它作血缘（[S3-04] 二：「携带 rule_id 血缘」）。
        rule_name: 规则名，如「夜间雨天急刹」。
        rule_type: 六大种类之一。
        expression_mode: SQL 或 可视化。
        sql_condition: expression_mode=SQL 时的手写条件。
        visual_config: expression_mode=VISUAL 时的条件树。
        rule_priority: 驱动向量化与存储分级。
        rule_version: 单调递增整数，每次实质修改 +1。
        execution_mode: 默认跟随 rule_type，可显式覆盖（仅模型输出类允许双模式）。
        scene_label: 命中后要打的场景标签（经统一标签服务做字典映射）。
        target_clip_count: 该场景的需求目标量，场景缺口识别用。
    """

    rule_id: str
    rule_name: str
    rule_type: RuleType
    expression_mode: ExpressionMode = ExpressionMode.VISUAL
    sql_condition: str = ""
    visual_config: Condition | None = None
    rule_status: RuleStatus = RuleStatus.DRAFT
    rule_priority: RulePriority = RulePriority.P2
    rule_version: int = 1
    execution_mode: ExecutionMode | None = None
    scene_label: str = ""
    target_clip_count: int = 0
    project_code: str = ""
    owner: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    disabled_at: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not _RULE_ID_RE.match(self.rule_id or ""):
            raise RuleValidationError(
                f"rule_id 非法: {self.rule_id!r}（字母开头，3-64 位字母/数字/下划线/连字符）"
            )
        if self.rule_version < 1:
            raise RuleValidationError("rule_version 从 1 起算")
        if self.target_clip_count < 0:
            raise RuleValidationError("target_clip_count 不能为负")
        mode = self.execution_mode or self.rule_type.default_execution_mode
        if not self.rule_type.allows(mode):
            raise RuleValidationError(
                f"规则种类「{self.rule_type.name_cn}」不支持执行模式 {mode.value}；"
                f"原文规定：{self.rule_type.spec.condition_note}"
            )
        object.__setattr__(self, "execution_mode", mode)

        if self.expression_mode is ExpressionMode.SQL:
            if not self.sql_condition.strip():
                raise RuleValidationError(
                    f"规则 {self.rule_id} 声明为 SQL 模式但 sql_condition 为空"
                )
        else:
            if self.visual_config is None:
                raise RuleValidationError(
                    f"规则 {self.rule_id} 声明为可视化模式但 visual_config 为空"
                )

    # ---- 条件收口：双模式表达在此汇成同一个 Condition ----

    def condition(self) -> Condition:
        """返回统一的条件对象——SQL 模式包成 RawSqlCondition，可视化模式直接返回条件树。

        这是原文「产出的规则等价」的落地点：下游编译器只认 Condition，
        不关心这条规则当初是敲出来的还是拖出来的。
        """
        if self.expression_mode is ExpressionMode.SQL:
            return RawSqlCondition(self.sql_condition)
        assert self.visual_config is not None  # __post_init__ 已保证
        return self.visual_config

    def where_sql(self, dialect: Dialect | None = None, *, alias: str = "") -> str:
        """渲染 WHERE 谓词（不含 WHERE 关键字）。"""
        d = dialect or self.effective_mode.dialect
        return self.condition().to_sql(d, alias=alias)

    @property
    def effective_mode(self) -> ExecutionMode:
        """实际执行模式（__post_init__ 已把默认值落实，这里只是类型收窄）。"""
        assert self.execution_mode is not None
        return self.execution_mode

    @property
    def vectorize_policy(self) -> VectorizePolicy:
        """命中数据进哪一档向量化队列——原文「优先级驱动下游」的直接体现。"""
        return self.rule_priority.vectorize_policy

    @property
    def is_runnable(self) -> bool:
        """调度器只挑 ENABLED 的规则跑。"""
        return self.rule_status is RuleStatus.ENABLED

    def condition_kinds(self) -> set[ConditionKind]:
        """条件树里用到的条件来源类型。"""
        cond = self.condition()
        if isinstance(cond, ConditionGroup):
            return cond.kinds()
        return {cond.kind}

    def fingerprint(self) -> str:
        """规则内容指纹：规则语义变了指纹才变，改个描述不算。

        用途有二：一是作为 artifact_id 的 content_hash 输入，让同一版本规则的重跑
        天然幂等（见 ids 模块规则一）；二是 CDC 同步时判断「这次变更是不是实质变更」。
        """
        payload = json.dumps(
            {
                "rule_id": self.rule_id,
                "rule_type": self.rule_type.value,
                "expression_mode": self.expression_mode.value,
                "condition": (
                    self.sql_condition.strip()
                    if self.expression_mode is ExpressionMode.SQL
                    else self.condition().to_dict()
                ),
                "execution_mode": self.effective_mode.value,
                "scene_label": self.scene_label,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return content_hash(payload, length=12)

    # ---- 生命周期：每个动作都产出一条留痕 ----

    def _changed(
        self, action: str, *, by: str, detail: str, at: datetime | None, bump: bool, **updates: Any
    ) -> tuple[RuleDefinition, RuleChange]:
        moment = at or datetime.now()
        to_version = self.rule_version + 1 if bump else self.rule_version
        new = replace(self, rule_version=to_version, updated_at=moment, **updates)
        change = RuleChange(
            rule_id=self.rule_id,
            action=action,
            from_version=self.rule_version,
            to_version=to_version,
            changed_at=moment,
            changed_by=by,
            detail=detail,
        )
        return new, change

    def enable(self, *, by: str, at: datetime | None = None) -> tuple[RuleDefinition, RuleChange]:
        """启用规则。启用不改语义，因此不 bump 版本。"""
        if self.rule_status is RuleStatus.ARCHIVED:
            raise RuleValidationError(f"规则 {self.rule_id} 已归档，不能直接启用")
        return self._changed(
            "enable",
            by=by,
            detail="规则启用",
            at=at,
            bump=False,
            rule_status=RuleStatus.ENABLED,
            disabled_at=None,
        )

    def disable(
        self, *, by: str, reason: str = "", at: datetime | None = None
    ) -> tuple[RuleDefinition, RuleChange]:
        """禁用规则。历史命中结果保留，配置保留，随时可再启用。"""
        moment = at or datetime.now()
        return self._changed(
            "disable",
            by=by,
            detail=reason or "规则禁用",
            at=moment,
            bump=False,
            rule_status=RuleStatus.DISABLED,
            disabled_at=moment,
        )

    def archive(
        self, *, by: str, reason: str = "", at: datetime | None = None
    ) -> tuple[RuleDefinition, RuleChange]:
        """归档规则：不再启用的终态，仅供审计追溯。"""
        return self._changed(
            "archive",
            by=by,
            detail=reason or "规则归档",
            at=at,
            bump=False,
            rule_status=RuleStatus.ARCHIVED,
        )

    def set_priority(
        self, priority: RulePriority, *, by: str, at: datetime | None = None
    ) -> tuple[RuleDefinition, RuleChange]:
        """调整优先级。会连带改变命中数据的向量化队列分级（[S3-04] 一）。"""
        detail = (
            f"优先级 {self.rule_priority.value} -> {priority.value}；"
            f"向量化分级 {self.vectorize_policy.value} -> {priority.vectorize_policy.value}"
        )
        return self._changed(
            "priority", by=by, detail=detail, at=at, bump=False, rule_priority=priority
        )

    def with_changes(
        self, *, by: str, detail: str = "", at: datetime | None = None, **updates: Any
    ) -> tuple[RuleDefinition, RuleChange]:
        """修改规则语义。版本 +1——老版本的命中结果因此仍可按版本对账。"""
        new, change = self._changed(
            "update", by=by, detail=detail or "规则修改", at=at, bump=True, **updates
        )
        if new.fingerprint() == self.fingerprint():
            # 指纹没变说明只动了描述性字段，回退版本号，避免版本号被无意义地推高
            new = replace(new, rule_version=self.rule_version)
            change = replace(
                change,
                to_version=self.rule_version,
                detail=f"{change.detail}（非语义变更，版本不变）",
            )
        return new, change

    # ---- 序列化 ----

    def to_row(self) -> dict[str, Any]:
        """渲染成 ods_mining_rule_config 的一行。

        列名用 registry 的权威名，本类的字段名只是引擎侧的领域名
        （``rule_type -> rule_category``、``scene_label -> target_tag_id`` …，
        映射表见 catalog/tables/_mining.py 的模块 docstring）。类型口径也在这里对齐：
        ``rule_version`` registry 是 STRING、``rule_priority`` 是 INT。

        本表的写入端是控制面 MySQL，引擎**不往湖仓写规则**（见模块 docstring），
        所以这里只渲染规则自身持有的那些列；``schedule_cron`` / ``create_user`` 等
        由控制面填，不在这里造假值。
        """
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "rule_category": self.rule_type.value,
            "rule_version": str(self.rule_version),
            "rule_priority": self.rule_priority.rank,
            "express_mode": self.expression_mode.value,
            "rule_sql": self.sql_condition,
            "rule_condition_json": (
                json.dumps(self.visual_config.to_dict(), ensure_ascii=False, sort_keys=True)
                if self.visual_config is not None
                else ""
            ),
            "exec_mode": self.effective_mode.value,
            "target_tag_id": self.scene_label,
            "target_clip_count": self.target_clip_count,
            "project_code": self.project_code,
            "rule_status": self.rule_status.value,
            "owner": self.owner,
            "create_time": self.created_at,
            "last_modify_time": self.updated_at,
            "disable_time": self.disabled_at,
        }

    def describe(self) -> str:
        """一行人类可读摘要，执行日志用。"""
        return (
            f"[{self.rule_priority.value}] {self.rule_id} 「{self.rule_name}」 "
            f"{self.rule_type.name_cn}/{self.effective_mode.label_cn} v{self.rule_version} "
            f"({self.rule_status.value}) -> 标签 {self.scene_label!r}"
        )


# --------------------------------------------------------------------------- 原文规则实例


@dataclass(frozen=True, slots=True)
class SignalSample:
    """CAN / 传感器信号流的一条采样——``vehicle_signal_stream`` 的一行。

    字段名逐字取自 :data:`~adas_lakehouse.mining.tables.VEHICLE_SIGNAL_STREAM` 的列契约。
    本模块**刻意不 import tables**（那会把 registry 与 config 拖进规则模型），
    两份定义的一致性由 tests/deep/test_mining.py 的
    ``test_signal_sample_fields_match_the_stream_contract`` 钉住——
    与 constants.py 里 keyframe 上下界的做法同一路数：用测试对账，不用 import 制造耦合。

    Attributes:
        data_id: 所属 clip，MATCH_RECOGNIZE 的 PARTITION BY 第一段。
        signal_name: 信号名，如 ``can_longitudinal_accel_mps2``。
        signal_value: 实测值。
        event_time: 采样时刻（流源的 ``event_time`` 列）。
        event_ts_ms: 毫秒时间戳。原文的持续时长是 **0.5 秒**——亚秒级，
            Flink 的 TIMESTAMPDIFF 只到秒，判不了，所以流侧专门要了这一列；
            Python 侧留空时由 :attr:`ts_ms` 从 event_time 现算。
    """

    data_id: str
    signal_name: str
    signal_value: float | None
    event_time: datetime
    event_ts_ms: int | None = None
    vehicle_code: str = ""
    project_code: str = ""

    @property
    def ts_ms(self) -> int:
        """毫秒时间戳。流里有 ``event_ts_ms`` 就用它，否则从 event_time 现算。

        只用于**同一段内作差**，所以 naive datetime 按本地时区换算不影响结果。
        """
        if self.event_ts_ms is not None:
            return int(self.event_ts_ms)
        return int(self.event_time.timestamp() * 1000)

    @property
    def partition_key(self) -> tuple[str, str, str]:
        """与流侧 ``PARTITION BY data_id, vehicle_code, project_code`` 同一把钥匙。"""
        return (self.data_id, self.vehicle_code, self.project_code)


@dataclass(frozen=True, slots=True)
class SustainedHit:
    """一段满足「阈值 + 持续时长」的信号命中。

    字段与流侧 MATCH_RECOGNIZE 的 ``MEASURES`` 一一对应（见
    :meth:`~adas_lakehouse.mining.compiler.RuleCompiler._render_match_recognize`）：
    ``anchor_time`` = FIRST(A.event_time)、``sustain_start_ms`` / ``sustain_end_ms``
    = FIRST/LAST(A.event_ts_ms)、``peak_value`` = MIN 或 MAX(A.signal_value)
    （取哪个见 :attr:`SignalCondition.peak_aggregate`）、``sample_count`` = COUNT(A)。

    Attributes:
        closed: 这段是否由「首个不再满足条件的采样」收尾。流侧的 ``PATTERN (A+ B)``
            必须等到 B 才出一条匹配，所以未收尾的尾段在流里只是**还没到**，不是没有。
    """

    data_id: str
    signal: str
    anchor_time: datetime
    sustain_start_ms: int
    sustain_end_ms: int
    peak_value: float
    sample_count: int
    vehicle_code: str = ""
    project_code: str = ""
    closed: bool = True

    @property
    def duration_sec(self) -> float:
        """持续时长（秒）= (LAST - FIRST) / 1000，与流侧的毫秒作差同一口径。"""
        return (self.sustain_end_ms - self.sustain_start_ms) / 1000.0

    def to_hit_row(self) -> dict[str, Any]:
        """渲染成 :meth:`~adas_lakehouse.mining.executor.StreamRuleExecutor.handle_hits`
        认识的命中行。

        ``peak_value`` 的键名与 MATCH_RECOGNIZE 的输出列同名——打分那一侧
        （``scoring.signal_severity``）就是按这个名字取实测值的。
        """
        return {
            "data_id": self.data_id,
            "event_time": self.anchor_time,
            "signal_name": self.signal,
            "peak_value": self.peak_value,
            "sample_count": self.sample_count,
            "sustain_duration_sec": self.duration_sec,
            "vehicle_code": self.vehicle_code,
            "project_code": self.project_code,
        }


def sustained_matches(
    condition: SignalCondition,
    samples: Iterable[SignalSample],
    *,
    emit_open_run: bool = False,
) -> tuple[SustainedHit, ...]:
    """在一串 CAN 采样上求值「阈值 + 持续 ≥ N 秒」，返回命中的时段。

    这是原文唯一带数字的那条规则（[S3-04] 二：「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，
    准实时」）在 Python 侧的求值实现，**与编译出来的 Flink MATCH_RECOGNIZE 逐条对齐**：

    ==============================  ===============================================
    MATCH_RECOGNIZE                 本函数
    ==============================  ===============================================
    先按 signal_name 预过滤          只看 ``signal_name == condition.signal`` 的采样
    PARTITION BY data/vehicle/proj   按 :attr:`SignalSample.partition_key` 分组
    ORDER BY event_time              组内按 event_time 稳定排序
    DEFINE A（阈值比较）             :meth:`SignalCondition.matches_point`
    PATTERN (A+ B)                   连续满足段 + 首个不再满足的采样收尾
    AFTER MATCH SKIP PAST LAST ROW   从收尾采样之后继续找下一段
    WHERE 末端毫秒差 >= N * 1000     :attr:`SustainedHit.duration_sec` ≥ N（含等于）
    ==============================  ===============================================

    Args:
        condition: 带 ``min_duration_sec`` 的信号条件。不带持续时长时退化成
            「单点命中即一段」（急变道这类瞬时尖峰规则走这条路）。
        samples: 采样序列，顺序随意，本函数自己排。
        emit_open_run: 序列末尾那段还没等到「不再满足」的采样时要不要出结果。
            默认 False = 与流侧严格一致（流里那段只是还没收尾，下一条采样到了自然会出）；
            离线批量复算一段已经录完的信号时传 True，否则最后一段会被无声吞掉。

    Returns:
        命中时段，按 (data_id, 起始时刻) 升序。

    Raises:
        RuleValidationError: 条件的持续时长为负或零（构造期已挡，这里是纵深）。
    """
    min_dur = condition.min_duration_sec
    if min_dur is not None and min_dur <= 0:
        raise RuleValidationError("持续时长必须为正")

    groups: dict[tuple[str, str, str], list[SignalSample]] = {}
    for s in samples:
        if s.signal_name != condition.signal:
            # 流侧在 MATCH_RECOGNIZE **之前**就按 signal_name 过滤：别的信号混在
            # 采样序列里会打断 A+ 的连续性，把好好的一段急减速切成两截。
            continue
        groups.setdefault(s.partition_key, []).append(s)

    take_max = condition.peak_aggregate == "MAX"
    hits: list[SustainedHit] = []
    for key, rows in groups.items():
        ordered = sorted(rows, key=lambda r: (r.event_time, r.ts_ms))
        run: list[SignalSample] = []
        for sample in ordered:
            if condition.matches_point(sample.signal_value):
                run.append(sample)
                continue
            if run:
                hit = _close_run(condition, key, run, min_dur, take_max, closed=True)
                if hit is not None:
                    hits.append(hit)
                run = []  # AFTER MATCH SKIP PAST LAST ROW
        if run and emit_open_run:
            hit = _close_run(condition, key, run, min_dur, take_max, closed=False)
            if hit is not None:
                hits.append(hit)
    hits.sort(key=lambda h: (h.data_id, h.sustain_start_ms))
    return tuple(hits)


def _close_run(
    condition: SignalCondition,
    key: tuple[str, str, str],
    run: Sequence[SignalSample],
    min_duration_sec: float | None,
    take_max: bool,
    *,
    closed: bool,
) -> SustainedHit | None:
    """把一段连续满足阈值的采样收成一条命中；不够时长就丢掉。"""
    start_ms, end_ms = run[0].ts_ms, run[-1].ts_ms
    # 原文是「持续 ≥ 0.5s」——等于 0.5 秒要算命中，所以判的是 <  才丢，不是 <=。
    # 毫秒作差，阈值现乘 1000：0.5 这个数字不在别处预先算成 500。
    if min_duration_sec is not None and (end_ms - start_ms) < min_duration_sec * 1000:
        return None
    values = [float(s.signal_value) for s in run if s.signal_value is not None]
    peak = max(values) if take_max else min(values)
    return SustainedHit(
        data_id=key[0],
        signal=condition.signal,
        anchor_time=run[0].event_time,
        sustain_start_ms=start_ms,
        sustain_end_ms=end_ms,
        peak_value=peak,
        sample_count=len(run),
        vehicle_code=key[1],
        project_code=key[2],
        closed=closed,
    )


def harsh_deceleration_condition() -> SignalCondition:
    """原文唯一给出具体阈值的规则条件：急减速。

    [S3-04] 二、六大种类表格「车辆信号」行逐字：
    「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」。

    阈值取自 :data:`~adas_lakehouse.mining.constants.HARSH_DECEL_THRESHOLD_MPS2`
    与 :data:`~adas_lakehouse.mining.constants.HARSH_DECEL_MIN_DURATION_SEC`，
    不在此处重写数字，保证全仓库只有一处定义。

    这条条件有三种等价的用法，三条路共用同一组数字：

    * 流式执行 —— 编译成 Flink MATCH_RECOGNIZE（``compiler._render_match_recognize``）；
    * 批式执行 —— 下推给自定义 UDF ``mining_signal_sustained``（[S3-04] 三）；
    * Python 求值 —— :func:`sustained_matches`，没有 Flink 也能把一串 CAN 采样
      判出命中，用于本地干跑、回放复算与单测钉死边界。

    ⚠️ 原文未明确，本项目设计：信号名 ``can_longitudinal_accel_mps2``。
    原文只说「CAN 减速度」，没给信号的字面名字；部署时按车端信号字典改这一处即可，
    阈值与持续时长不许跟着改。
    """
    return SignalCondition(
        signal="can_longitudinal_accel_mps2",
        op=CompareOp.LT,  # 原文是严格小于
        threshold=HARSH_DECEL_THRESHOLD_MPS2,  # -4.0 m/s²
        min_duration_sec=HARSH_DECEL_MIN_DURATION_SEC,  # ≥ 0.5s
    )


def _sample_rules() -> tuple[RuleDefinition, ...]:
    """按原文六大种类表格的示例，各造一条可直接跑的规则。

    ⚠️ 原文只给了规则的「名字」（雨天高速、夜间雾天、城市行人场景……），
    没有给出各自的字段与取值。因此除急减速的 -4m/s² / 0.5s 与事件窗口的 15/5 秒外，
    下面的具体列名、标签取值、经纬度边界、时段边界均为本项目填充的示例值，
    仅用于单测与演示，不要当成原文方案。
    """
    return (
        # 1. 标签组合：雨天高速
        RuleDefinition(
            rule_id="RULE_TAG_RAINY_HIGHWAY",
            rule_name="雨天高速",
            rule_type=RuleType.TAG_COMBINATION,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P1,
            scene_label="rainy_highway",
            target_clip_count=5000,
            visual_config=ConditionGroup(
                "AND",
                (
                    TagCondition("weather", ("rain", "heavy_rain")),
                    TagCondition("road_type", ("highway",)),
                ),
            ),
            notes="原文示例：采集标签多字段 AND 组合，T+1 批",
        ),
        # 1'. 标签组合：夜间雾天
        RuleDefinition(
            rule_id="RULE_TAG_NIGHT_FOG",
            rule_name="夜间雾天",
            rule_type=RuleType.TAG_COMBINATION,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P1,
            scene_label="night_fog",
            target_clip_count=3000,
            visual_config=ConditionGroup(
                "AND",
                (
                    TagCondition("light_condition", ("night",)),
                    TagCondition("weather", ("fog", "heavy_fog")),
                ),
            ),
        ),
        # 2. 时空地理：城市行人场景
        RuleDefinition(
            rule_id="RULE_GEO_URBAN_PEDESTRIAN",
            rule_name="城市行人场景",
            rule_type=RuleType.SPATIOTEMPORAL,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P0,
            scene_label="urban_pedestrian",
            target_clip_count=8000,
            visual_config=ConditionGroup(
                "AND",
                (
                    GeoFenceCondition(
                        shape="bbox",
                        fence_name="上海市区",
                        min_lat=31.10,
                        max_lat=31.40,
                        min_lon=121.35,
                        max_lon=121.65,
                    ),
                    TagCondition("road_type", ("urban", "urban_intersection")),
                ),
            ),
        ),
        # 2'. 时空地理：通勤高峰
        RuleDefinition(
            rule_id="RULE_GEO_RUSH_HOUR",
            rule_name="通勤高峰",
            rule_type=RuleType.SPATIOTEMPORAL,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P2,
            scene_label="commute_rush_hour",
            target_clip_count=4000,
            visual_config=ConditionGroup(
                "OR",
                (
                    TimeWindowCondition(
                        start_time=time(7, 30), end_time=time(9, 30), weekdays=(1, 2, 3, 4, 5)
                    ),
                    TimeWindowCondition(
                        start_time=time(17, 30), end_time=time(19, 30), weekdays=(1, 2, 3, 4, 5)
                    ),
                ),
            ),
        ),
        # 3. 车辆信号：急减速（原文唯一给数字的规则）
        RuleDefinition(
            rule_id="RULE_SIGNAL_HARSH_DECEL",
            rule_name="急减速",
            rule_type=RuleType.VEHICLE_SIGNAL,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P0,
            scene_label="harsh_deceleration",
            target_clip_count=2000,
            visual_config=harsh_deceleration_condition(),
            notes="原文逐字：CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时",
        ),
        # 4. 模型输出：AEB 触发（原文注明可 T+1 批或准实时，这里取批）
        RuleDefinition(
            rule_id="RULE_MODEL_AEB_TRIGGER",
            rule_name="AEB 触发",
            rule_type=RuleType.MODEL_OUTPUT,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P0,
            execution_mode=ExecutionMode.BATCH_T_PLUS_1,
            scene_label="aeb_triggered",
            target_clip_count=1500,
            visual_config=ModelOutputCondition(output_column="aeb_triggered", value=True),
        ),
        # 5. 事件触发：驾驶员接管（含前 15 后 5 秒窗口）
        RuleDefinition(
            rule_id="RULE_EVENT_DRIVER_TAKEOVER",
            rule_name="驾驶员接管",
            rule_type=RuleType.EVENT_TRIGGER,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P0,
            scene_label="driver_takeover",
            target_clip_count=1000,
            visual_config=EventCondition(trigger_types=("driver_takeover",)),
            notes="原文逐字：消费回传触发事件流，含前 15 后 5 秒窗口，准实时",
        ),
        # 6. 多条件复合：夜间雨天急刹（标签 + 信号叠加）
        RuleDefinition(
            rule_id="RULE_COMPOSITE_NIGHT_RAIN_BRAKE",
            rule_name="夜间雨天急刹",
            rule_type=RuleType.COMPOSITE,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P0,
            scene_label="night_rain_harsh_brake",
            target_clip_count=800,
            visual_config=ConditionGroup(
                "AND",
                (
                    TagCondition("light_condition", ("night",)),
                    TagCondition("weather", ("rain", "heavy_rain")),
                    harsh_deceleration_condition(),
                ),
            ),
            notes="原文逐字：标签 + 信号多条件叠加，T+1 批",
        ),
        # 工程师写 SQL 的那一模式（双模式表达的另一半）
        RuleDefinition(
            rule_id="RULE_SQL_PEDESTRIAN_NEAR_MISS",
            rule_name="行人险肇",
            rule_type=RuleType.MODEL_OUTPUT,
            rule_status=RuleStatus.ENABLED,
            rule_priority=RulePriority.P0,
            expression_mode=ExpressionMode.SQL,
            execution_mode=ExecutionMode.BATCH_T_PLUS_1,
            sql_condition="pedestrian_min_ttc_sec < 1.5 AND pedestrian_track_count > 0",
            scene_label="pedestrian_near_miss",
            target_clip_count=1200,
        ),
    )


#: 六大种类各一条的示例规则集。单测与本地演示用。
SAMPLE_RULES: tuple[RuleDefinition, ...] = _sample_rules()


def rules_by_mode(
    rules: Sequence[RuleDefinition], mode: ExecutionMode
) -> tuple[RuleDefinition, ...]:
    """按执行模式筛选可运行的规则——批流双模调度的入口。"""
    return tuple(r for r in rules if r.is_runnable and r.effective_mode is mode)


def sort_by_priority(rules: Sequence[RuleDefinition]) -> tuple[RuleDefinition, ...]:
    """按优先级排序（P0 在前）。同档按 rule_id 稳定排序。

    原文（[S3-04] 一）：「rule_priority 不只是排序字段」——它首先仍然是排序字段。
    """
    return tuple(sorted(rules, key=lambda r: (r.rule_priority.rank, r.rule_id)))
