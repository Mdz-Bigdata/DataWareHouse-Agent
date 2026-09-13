"""11 个数据域：智驾数据闭环的「户口本」。

数据域 = 闭环 8 个业务环节 + 3 个支撑域，每个域一个简短英文词根，
作为四段式表名的第二段。见 naming.py。

来源：系列二《数仓命名规范 + 11 数据域划分 + 分区策略全解》第二章。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Layer(str, Enum):
    """数仓分层。每层只干一件事，核心价值是变更隔离。"""

    ODS = "ods"
    DWD = "dwd"
    DWS = "dws"
    ADS = "ads"

    @property
    def purpose(self) -> str:
        return _LAYER_PURPOSE[self]

    @property
    def system_fields(self) -> tuple[str, ...]:
        """系统字段规范：ODS 用 _source_system，其余层用 update_time。

        ODS 层不做业务更新，记录「数据从哪个系统来」比「什么时候更新」更有意义。
        """
        if self is Layer.ODS:
            return ("_ingest_time", "_source_system")
        return ("_ingest_time", "update_time")


_LAYER_PURPOSE: dict[Layer, str] = {
    Layer.ODS: "原始同步层：源系统什么样就存什么样，不加业务逻辑，只做字段映射与系统字段补充",
    Layer.DWD: "明细数据层：以 data_id 串联闭环链路，跨源 JOIN、清洗、标准化，血缘与追溯的核心层",
    Layer.DWS: "汇总指标层：按业务维度预聚合，口径在此层固化，下游不再重复计算",
    Layer.ADS: "应用数据层：面向报表/大屏/应用，零 JOIN，开箱即用",
}


@dataclass(frozen=True, slots=True)
class Domain:
    """一个数据域。

    prefix 是表名第二段；ordinal 对应文章中 11 数据域表的编号。
    """

    ordinal: int
    key: str
    prefix: str
    name_cn: str
    meaning: str

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"{self.ordinal:02d} {self.name_cn}({self.prefix})"


class DataDomain(Enum):
    """11 个数据域。

    值是一个 Domain 记录，常用字段通过属性代理出来，便于 ``DataDomain.MINING.prefix``。

    注意：闭环域没有 ODS 表——它是跨域整合域，数据全部来自其他域 DWD 层加工。
    """

    COLLECT = Domain(1, "collect", "collect_", "采集域", "采集任务/车辆/传感器/clip")
    PRODUCTION = Domain(2, "production", "production_", "生产域", "产线/标注/质检/效率")
    DATASET = Domain(3, "dataset", "dataset_", "数据资产域", "数据集/场景标签/资产目录")
    TRAINING = Domain(4, "training", "training_", "训练域", "训练任务/指标/模型版本")
    EVALUATION = Domain(5, "evaluation", "evaluation_", "评测域", "评测/Badcase/根因分布")
    SIMULATION = Domain(6, "simulation", "simulation_", "仿真域", "仿真场景与运行结果")
    TRIGGER = Domain(7, "trigger", "trigger_", "回传域", "触发事件/影子模式/热力图")
    DEPLOYMENT = Domain(8, "deployment", "deployment_", "部署域", "OTA 部署/车端版本")
    ISSUE = Domain(9, "issue", "issue_", "分析域", "问题记录与分析")
    MINING = Domain(10, "mining", "mining_", "挖掘域", "挖掘/抽帧/标签/向量/推理")
    CLOSED_LOOP = Domain(11, "closed_loop", "closed_loop_", "闭环域", "追溯/存储生命周期/成本")

    @property
    def ordinal(self) -> int:
        return self.value.ordinal

    @property
    def key(self) -> str:
        return self.value.key

    @property
    def prefix(self) -> str:
        return self.value.prefix

    @property
    def name_cn(self) -> str:
        return self.value.name_cn

    @property
    def meaning(self) -> str:
        return self.value.meaning

    @classmethod
    def by_prefix(cls, prefix: str) -> DataDomain:
        """按域前缀查域，接受 ``mining_`` 和 ``mining`` 两种写法。"""
        want = prefix.rstrip("_")
        for d in cls:
            if d.key == want:
                return d
        raise KeyError(f"未知数据域前缀: {prefix!r}")

    @classmethod
    def longest_prefix_match(cls, table_name: str) -> DataDomain | None:
        """从表名中切出数据域。

        必须按前缀长度倒序匹配：``closed_loop_`` 会被 ``collect_`` 之外的短前缀误伤，
        且 ``dataset_`` 与 ``data_`` 这类包含关系也要靠最长匹配区分。
        """
        body = table_name.split("_", 1)[1] if "_" in table_name else table_name
        for d in sorted(cls, key=lambda x: len(x.prefix), reverse=True):
            if body.startswith(d.prefix):
                return d
        return None


#: 表名里出现、但不属于 11 数据域的特例。
#: 质量门禁的异常隔离表 ods_quality_issue 独立于数据域之外——它是入湖闸门的产物，
#: 命中硬规则的数据在此隔离等待处置，保证可重放。
QUALITY_GATE_PSEUDO_DOMAIN = "quality_"
