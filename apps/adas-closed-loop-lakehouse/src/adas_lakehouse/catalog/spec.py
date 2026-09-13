"""表规格：一张 Paimon 表的完整声明，以及它到 DDL 的渲染。

物理存储策略全部来自系列二《命名规范 + 数据域 + 分区策略》（[a12]）第三~五章：
  · 分区决策三规则（大体量按 dt / 有业务分类按业务字段 / Upsert 无维度则不分区）
  · Bucket 五档（1 / 2 / 4 / 8 / 16）
  · changelog-producer 三选一（input / lookup / full-compaction）
  · 主键三原则（业务主键优先 / 复合主键表达粒度 / 分区表主键必须含分区字段）
  · 系统字段规范（ODS: _ingest_time + _source_system；其余: _ingest_time + update_time）

本模块把这四张决策表**逐字**落成常量（``BUCKET_TIERS`` / ``CHANGELOG_MODES`` /
``PARTITION_RULES`` / ``PARTITION_PANORAMA`` / ``PRIMARY_KEY_PRINCIPLES``），
再由 ``TableSpec.validate()`` 逐条硬校验，``registry.reconcile_source_matrices()``
则反向核对「原文点名的代表表在本注册表里是否真取了那一档」。
常量只被复述一次——决策表改了，校验与对账同时跟着改，不会两边漂移。

⚠️ 与其他源文的已登记冲突（详见 docs/source-deviations.md）：
  · A-4  ：[a10] 第五章②「DWD 层核心表 16-32 个 bucket」、[a13] 3.1 DDL 写死
           ``'bucket' = '32'``；本项目取 [a12] 五档，``dwd_production_artifact_detail``
           取 16（五档是封闭集合，可硬校验；区间式描述不可校验）。
  · A-4-1：[a13] 3.1 的 DDL 节选给 DWD 表写 ``'changelog-producer' = 'input'``，
           与 [a12] 第四章「DWD 有 Upsert → lookup」口诀冲突；本项目取 [a12]，
           DWD 30 张一律 ``lookup``（且 [a13] 自述的 Upsert 语义要求的正是 lookup）。
  · A-5  ：[a10] 第三章③「全湖唯一的分区表」vs [a12] 分区全景表 6 行；取 6 行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from ..domains import DataDomain, Layer
from ..naming import lint, parse

__all__ = [
    "BUCKET_TIERS",
    "CHANGELOG_DECISION_MNEMONIC",
    "CHANGELOG_MISCHOICE_CONSEQUENCE",
    "CHANGELOG_MODES",
    "DATE_PARTITION_FIELD",
    "NON_BUSINESS_PK_NAMES",
    "PARTITION_PANORAMA",
    "PARTITION_RULES",
    "PRIMARY_KEY_PRINCIPLES",
    "RESERVED_TABLE_OPTIONS",
    "SYSTEM_COLUMNS",
    "SYSTEM_FIELD_SPEC",
    "VARIANT_MIN_ENGINE",
    "VARIANT_TYPE",
    "BucketTier",
    "ChangelogMode",
    "ChangelogProducer",
    "Column",
    "PartitionEntry",
    "PartitionRule",
    "PartitionRuleSpec",
    "PrimaryKeyPrinciple",
    "TableSpec",
    "partition_rule_for",
    "sql_literal",
]


# --------------------------------------------------------------------------- SQL 字面量


def sql_literal(text: str) -> str:
    """把任意文本包成 Flink SQL 单引号字符串字面量。

    单引号按 SQL 标准双写转义。不转义会直接产出语法非法的 DDL——注册表里的列注释
    真的含单引号（如 ``CONCAT(run_id, '_', data_id)``、``stage='mining'``），
    渲染出来的 ``COMMENT '... '_' ...'`` 会在第二个引号处提前截断。
    """
    return "'" + text.replace("'", "''") + "'"


# --------------------------------------------------------------------------- changelog 三选一


class ChangelogProducer(str, Enum):
    """[a12] 第四章「changelog-producer 三选一」。

    决策口诀：ODS 从 CDC 来 → input；DWD 有 Upsert → lookup；DWS/ADS 批量聚合 →
    full-compaction。

    选错最直接的后果：该用 lookup 的表用了 input，下游拿到的 changelog 缺少 -U 记录，
    增量同步数据不一致。

    每档的「适用场景 / 原理」逐字见 :data:`CHANGELOG_MODES`。
    """

    INPUT = "input"
    LOOKUP = "lookup"
    FULL_COMPACTION = "full-compaction"

    @classmethod
    def default_for(cls, layer: Layer) -> ChangelogProducer:
        if layer is Layer.ODS:
            return cls.INPUT
        if layer is Layer.DWD:
            return cls.LOOKUP
        return cls.FULL_COMPACTION


#: [a12] 第四章 changelog-producer 决策口诀（原文原话）。
CHANGELOG_DECISION_MNEMONIC = (
    "ODS 从 CDC 来 → input；DWD 有 Upsert → lookup；DWS/ADS 批量聚合 → full-compaction"
)

#: [a12] 第四章「选错」的后果（原文原话）——这条是 changelog 硬校验存在的理由。
CHANGELOG_MISCHOICE_CONSEQUENCE = (
    "选错最直接的后果：该用 lookup 的表用了 input，下游拿到的 changelog 缺少 -U 记录，"
    "增量同步数据不一致。"
)


@dataclass(frozen=True, slots=True)
class ChangelogMode:
    """changelog-producer 三选一决策表的一行（[a12] 第四章，逐字）。"""

    producer: ChangelogProducer
    #: 「适用场景」列，原文原话
    scenario: str
    #: 「原理」列，原文原话
    principle: str
    #: 口诀里这一档归属的层级
    layers: tuple[Layer, ...]


#: changelog-producer 三选一决策表（[a12] 第四章，三行逐字）。
CHANGELOG_MODES: dict[ChangelogProducer, ChangelogMode] = {
    ChangelogProducer.INPUT: ChangelogMode(
        producer=ChangelogProducer.INPUT,
        scenario="ODS CDC 透传 / Append-only 明细",
        principle="上游写入本身就是完整 changelog，直接透传，零额外开销",
        layers=(Layer.ODS,),
    ),
    ChangelogProducer.LOOKUP: ChangelogMode(
        producer=ChangelogProducer.LOOKUP,
        scenario="DWD Upsert 表（需对比旧值）",
        principle="写入时 lookup 旧值产生 -U/+U 变更，适合状态频繁更新的链路表",
        layers=(Layer.DWD,),
    ),
    ChangelogProducer.FULL_COMPACTION: ChangelogMode(
        producer=ChangelogProducer.FULL_COMPACTION,
        scenario="DWS/ADS 离线聚合",
        principle="在 full-compaction 时产生 changelog，适合批量写入、低频更新的聚合表",
        layers=(Layer.DWS, Layer.ADS),
    ),
}


# --------------------------------------------------------------------------- Bucket 五档


@dataclass(frozen=True, slots=True)
class BucketTier:
    """Bucket 五档决策表的一行（[a12] 第四章，逐字）。"""

    #: 「档位」列
    bucket: int
    #: 「适用场景」列，原文原话
    scenario: str
    #: 「典型层级」列
    typical_layers: tuple[Layer, ...]
    #: 「代表表」列，原文点名的表名
    example_tables: tuple[str, ...]

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"bucket {self.bucket}：{self.scenario}"


#: Bucket 五档决策表（[a12] 第四章，五行逐字：档位 / 适用场景 / 典型层级 / 代表表）。
#: 键是档位本身——``bucket in BUCKET_TIERS`` 就是五档的封闭集合校验。
#: 太小则单文件过大查询慢，太大则小文件过多 Compaction 压力大，所以是封闭集合而非区间。
BUCKET_TIERS: dict[int, BucketTier] = {
    1: BucketTier(
        bucket=1,
        scenario="字典表/极小表",
        typical_layers=(Layer.ADS,),
        example_tables=("ads_storage_cost_dashboard",),
    ),
    2: BucketTier(
        bucket=2,
        scenario="DWS 汇总表 / 小 ADS",
        typical_layers=(Layer.DWS, Layer.ADS),
        example_tables=("dws_production_efficiency_daily",),
    ),
    4: BucketTier(
        bucket=4,
        scenario="中等体量 ODS/DWD",
        typical_layers=(Layer.ODS, Layer.DWD),
        example_tables=("ods_collect_task", "dwd_training_task_detail"),
    ),
    8: BucketTier(
        bucket=8,
        scenario="大体量明细表",
        typical_layers=(Layer.DWD,),
        example_tables=("dwd_production_execution_detail",),
    ),
    16: BucketTier(
        bucket=16,
        scenario="超大表 / 高并发写入",
        typical_layers=(Layer.DWD,),
        example_tables=("dwd_data_production_chain", "dwd_mining_image_vector_detail"),
    ),
}


# --------------------------------------------------------------------------- 分区决策三规则


class PartitionRule(str, Enum):
    """[a12] 第三章「分区决策三规则」。"""

    #: 规则一：大体量 + 时间范围查询 → 按 dt 分区
    BY_DATE = "rule_1_by_dt"
    #: 规则二：有明确业务分类过滤 → 按业务字段分区
    BY_BUSINESS_FIELD = "rule_2_by_business_field"
    #: 规则三：主键 Upsert + 无明确分区维度 → 不分区
    NO_PARTITION = "rule_3_no_partition"


@dataclass(frozen=True, slots=True)
class PartitionRuleSpec:
    """分区决策三规则的一条（[a12] 第三章，逐字）。"""

    rule: PartitionRule
    #: 规则标题，原文原话
    title: str
    #: 规则正文（代表表与理由），原文原话
    detail: str


#: 分区决策三规则（[a12] 第三章，三条逐字）。
PARTITION_RULES: dict[PartitionRule, PartitionRuleSpec] = {
    PartitionRule.BY_DATE: PartitionRuleSpec(
        rule=PartitionRule.BY_DATE,
        title="规则一：大体量 + 时间范围查询 → 按 dt 分区",
        detail=(
            "代表：dwd_mining_image_vector_detail（千万~亿级），"
            "按 dt 分区支撑降冷与向量索引增量刷新。"
        ),
    ),
    PartitionRule.BY_BUSINESS_FIELD: PartitionRuleSpec(
        rule=PartitionRule.BY_BUSINESS_FIELD,
        title="规则二：有明确业务分类过滤 → 按业务字段分区",
        detail=(
            "代表：ods_vehicle_trigger_event 按 trigger_type，"
            "dwd_evaluation_result_detail 按 evaluation_type。"
        ),
    ),
    PartitionRule.NO_PARTITION: PartitionRuleSpec(
        rule=PartitionRule.NO_PARTITION,
        title="规则三：主键 Upsert + 无明确分区维度 → 不分区",
        detail=("绝大多数 DWD/DWS/ADS 表属于此类。通过 Bucket 打散，不分区避免小文件过多。"),
    ),
}

#: 规则一的分区字段名。日期分区全湖只用这一个列名。
DATE_PARTITION_FIELD = "dt"


@dataclass(frozen=True, slots=True)
class PartitionEntry:
    """分区全景表的一行（[a12] 第三章，逐字：表名 / 分区字段 / 类型 / 原因）。"""

    table: str
    #: 「分区字段」列
    field: str
    #: 「类型」列：日期 | 业务字段
    kind: str
    #: 「原因」列，原文原话
    reason: str

    @property
    def rule(self) -> PartitionRule:
        return PartitionRule.BY_DATE if self.kind == "日期" else PartitionRule.BY_BUSINESS_FIELD


#: 分区全景表（[a12] 第三章，六行逐字）。全湖**恰好**这 6 张分区表，其余全部走规则三。
#: 顺序照抄原文表格，便于逐行比对。
PARTITION_PANORAMA: tuple[PartitionEntry, ...] = (
    PartitionEntry("dwd_mining_image_vector_detail", "dt", "日期", "全湖最大表，按天降冷+索引刷新"),
    PartitionEntry("ods_quality_issue", "dt", "日期", "异常隔离表，按天 TTL 清理"),
    PartitionEntry("ods_vehicle_trigger_event", "trigger_type", "业务字段", "按触发类型过滤"),
    PartitionEntry(
        "dwd_evaluation_result_detail", "evaluation_type", "业务字段", "离线/仿真/实车差异大"
    ),
    PartitionEntry("ods_data_file_meta", "file_type", "业务字段", "文件类型差异大"),
    PartitionEntry("ods_production_kafka_event", "event_type", "业务字段", "事件类型数据量差异大"),
)


def partition_rule_for(partition_by: tuple[str, ...]) -> PartitionRule:
    """按 [a12] 第三章三规则给分区方案归档。

    :param partition_by: 表的分区字段元组（空元组表示不分区）。
    :returns: 命中的规则。不分区 → 规则三；按 ``dt`` → 规则一；其余 → 规则二。
    :raises ValueError: 多级分区。本项目 6 张分区表全是单字段分区（见 source-deviations
        A-5：[a5] 第四章的「dt 主分区 + 维度辅分区」二级分区方案未采用）。
    """
    if not partition_by:
        return PartitionRule.NO_PARTITION
    if len(partition_by) > 1:
        raise ValueError(
            f"多级分区 {partition_by} 不在分区决策三规则内："
            f"本项目按 [a12] 第三章分区全景表只做单字段分区"
        )
    if partition_by[0] == DATE_PARTITION_FIELD:
        return PartitionRule.BY_DATE
    return PartitionRule.BY_BUSINESS_FIELD


# --------------------------------------------------------------------------- 主键三原则


@dataclass(frozen=True, slots=True)
class PrimaryKeyPrinciple:
    """主键设计三原则之一（[a12] 第五章，逐字）。"""

    ordinal: int
    #: 原则标题，原文原话
    title: str
    #: 原则正文（含原文点名的例子），原文原话
    detail: str
    #: 原文点名的示例表 → 主键
    examples: tuple[tuple[str, tuple[str, ...]], ...] = ()


#: 主键设计三原则（[a12] 第五章，三条逐字）。
PRIMARY_KEY_PRINCIPLES: tuple[PrimaryKeyPrinciple, ...] = (
    PrimaryKeyPrinciple(
        ordinal=1,
        title="原则一：业务主键优先",
        detail=(
            "用业务含义明确的字段做主键，不用自增 ID。如 data_id、training_task_id，"
            "下游一看就知道代表什么。"
        ),
        examples=(
            ("dwd_data_production_chain", ("data_id",)),
            ("dwd_training_task_detail", ("training_task_id",)),
        ),
    ),
    PrimaryKeyPrinciple(
        ordinal=2,
        title="原则二：复合主键表达完整粒度",
        detail=(
            "当单字段无法唯一标识时，用复合主键。如 dwd_dataset_version_detail 的 PK 是 "
            "(dataset_id, version)——同一个数据集可以有多个版本。"
        ),
        examples=(("dwd_dataset_version_detail", ("dataset_id", "version")),),
    ),
    PrimaryKeyPrinciple(
        ordinal=3,
        title="原则三：分区表主键必须含分区字段",
        detail=(
            "Paimon 要求分区表的主键包含分区字段。如 dwd_mining_image_vector_detail "
            "按 dt 分区，PK 为 (image_id, embedding_version, dt)。"
        ),
        examples=(("dwd_mining_image_vector_detail", ("image_id", "embedding_version", "dt")),),
    ),
)

#: 原则一的反面清单：没有业务含义的自增/代理键列名，一律不许当主键。
#: 「不用自增 ID」是原文原话，这里把它落成可校验的黑名单。
NON_BUSINESS_PK_NAMES: frozenset[str] = frozenset(
    {"id", "pk", "seq", "seq_no", "seqno", "auto_id", "autoid", "row_id", "rowid", "uuid", "guid"}
)


# --------------------------------------------------------------------------- 字段与系统字段


#: 半结构化列用的类型字面量。全湖只有 dwd_mining_image_vector_detail.vector_meta 用它。
VARIANT_TYPE: Final[str] = "VARIANT"

#: VARIANT 列对执行引擎的最低要求（[a9] 第 03 节）。
#:
#: **这不是理论值，是实测出来的**：本仓 ``docker/flink/Dockerfile`` 钉的是
#: Flink 1.20.1 + Paimon 1.0.1，在该栈上执行 ``ddl/20_dwd.sql`` 会在
#: ``dwd_mining_image_vector_detail`` 这一条上报::
#:
#:     org.apache.calcite.sql.validate.SqlValidatorException:
#:     Unknown identifier 'VARIANT'
#:
#: 报错来自 Flink 的 SQL 解析器（Calcite），不是 Paimon——换 Paimon 版本救不了，
#: 必须是 Flink 本身认识 VARIANT 这个类型。同一份 DDL 里另外 87 张表在该栈上全部建表成功，
#: 所以这是**单点**缺口，不是全局不可用。见 source-deviations A-12。
VARIANT_MIN_ENGINE: Final[dict[str, str]] = {
    "flink": "2.1",
    "spark": "4.0",
    "file_format": "parquet",
}


@dataclass(frozen=True, slots=True)
class Column:
    """一个字段。type 用 Flink/Paimon SQL 类型字面量。"""

    name: str
    type: str
    comment: str = ""
    nullable: bool = True

    def render(self, *, variant_fallback_type: str | None = None) -> str:
        """渲染一行列定义。

        :param variant_fallback_type: **默认 None = 不降级**，渲染结果与从前逐字节一致。
            给一个类型字面量（实践里是 ``STRING``）时，:data:`VARIANT_TYPE` 列改用该类型
            渲染，并在列注释里留下降级痕迹——这是给钉在 Flink
            < :data:`VARIANT_MIN_ENGINE`\\ ``['flink']`` 的部署留的逃生口，
            默认关闭，不改变任何既有产物。
        """
        col_type = self.type
        comment = self.comment
        if variant_fallback_type and col_type.upper() == VARIANT_TYPE:
            col_type = variant_fallback_type
            marker = (
                f"[{VARIANT_TYPE}→{variant_fallback_type} 降级："
                f"本引擎低于 Flink {VARIANT_MIN_ENGINE['flink']}，"
                f"按 JSON 文本承接，shredding 与 variant_get 下推不可用]"
            )
            comment = f"{comment} {marker}" if comment else marker
        null = "" if self.nullable else " NOT NULL"
        cmt = f" COMMENT {sql_literal(comment)}" if comment else ""
        return f"  `{self.name}` {col_type}{null}{cmt}"


#: 系统字段定义（两组，按层选用）。见 domains.Layer.system_fields。
SYSTEM_COLUMNS: dict[str, Column] = {
    "_ingest_time": Column("_ingest_time", "TIMESTAMP(3)", "入湖时间", nullable=False),
    "_source_system": Column("_source_system", "STRING", "来源系统标识", nullable=False),
    "update_time": Column("update_time", "TIMESTAMP(3)", "业务更新时间"),
}

#: 系统字段规范（[a12] 第五章，两行逐字：层级 / 系统字段 / 用途）。
#: ODS 层用 _source_system 换掉 update_time——ODS 层不做业务更新，
#: 记录「数据从哪个系统来」比「什么时候更新」更有意义。
SYSTEM_FIELD_SPEC: dict[Layer, tuple[tuple[str, ...], str]] = {
    Layer.ODS: (
        ("_ingest_time", "_source_system"),
        "入湖时间 + 来源系统标识，支持多源追溯",
    ),
    Layer.DWD: (
        ("_ingest_time", "update_time"),
        "入湖时间 + 业务更新时间，支持增量同步与变更追踪",
    ),
    Layer.DWS: (
        ("_ingest_time", "update_time"),
        "入湖时间 + 业务更新时间，支持增量同步与变更追踪",
    ),
    Layer.ADS: (
        ("_ingest_time", "update_time"),
        "入湖时间 + 业务更新时间，支持增量同步与变更追踪",
    ),
}

#: 建表 WITH 子句里不许出现的 key——这几项由 TableSpec 的专有字段负责渲染，
#: 放进 extra_options 会绕过 Bucket 五档 / changelog 三选一 / 主键 / 分区的硬校验。
RESERVED_TABLE_OPTIONS: frozenset[str] = frozenset(
    {"bucket", "changelog-producer", "primary-key", "partition"}
)


# --------------------------------------------------------------------------- 表规格


@dataclass(slots=True)
class TableSpec:
    """一张表的完整规格。

    columns 只写业务字段——系统字段由 all_columns() 按层自动追加，避免 88 张表逐个重复。
    """

    name: str
    layer: Layer
    domain: DataDomain
    comment: str
    columns: list[Column]
    primary_key: tuple[str, ...]
    bucket: int
    partition_by: tuple[str, ...] = ()
    changelog_producer: ChangelogProducer | None = None
    source_system: str = ""
    notes: str = ""
    #: 该表的字段定义是否为本项目推断（文章只给了表名与语义，未给完整 DDL）
    fields_inferred: bool = True
    #: 表名不含数据域段（第二段）——源文中不少表名如此（ods_vehicle_info、
    #: ads_hard_case_library）。置 True 表示这是已知并接受的偏离，不再重复告警。
    name_omits_domain: bool = False
    #: 表名不含标准粒度后缀（第四段）——源文给定的表名如 dws_closed_loop_efficiency、
    #: dws_data_contribution 即如此。同样置 True 登记为已知偏离。
    name_omits_suffix: bool = False
    #: 非 11 数据域的伪域标记（目前只有质量门禁 "quality_"）。参与表数对账时单列。
    pseudo_domain: str = ""
    extra_options: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.changelog_producer is None:
            self.changelog_producer = ChangelogProducer.default_for(self.layer)

    # ---- 组装 ----

    def all_columns(self) -> list[Column]:
        """业务字段 + 该层的系统字段（去重，业务字段优先）。"""
        seen = {c.name for c in self.columns}
        out = list(self.columns)
        for fname in self.layer.system_fields:
            if fname not in seen:
                out.append(SYSTEM_COLUMNS[fname])
        return out

    @property
    def partition_rule(self) -> PartitionRule:
        """本表命中的分区决策规则（[a12] 第三章三规则）。

        :raises ValueError: 多级分区——不在三规则内，由 validate() 报成违规。
        """
        return partition_rule_for(self.partition_by)

    @property
    def bucket_tier(self) -> BucketTier | None:
        """本表 bucket 对应的五档记录；不在五档内则为 None。"""
        return BUCKET_TIERS.get(self.bucket)

    # ---- 校验 ----

    def naming_notes(self) -> list[str]:
        """非阻断的命名提示，用于批量审计「有多少表偏离了四段式」。"""
        return lint(self.name, expected_layer=self.layer)

    def bucket_notes(self) -> list[str]:
        """非阻断的 bucket 提示：档位用在了「典型层级」之外。

        不做硬校验是有意的——原文自己就有反例（dwd_mining_tag_dict_detail 是 DWD 层的
        受控词表，按「字典表/极小表」取 1 档，而 1 档的典型层级写的是 ADS）。
        「典型」是惯例不是红线，红线只有「必须落在五档之内」。
        """
        tier = self.bucket_tier
        if tier is None:
            return []
        if self.layer in tier.typical_layers:
            return []
        return [
            f"bucket {self.bucket}（{tier.scenario}）的典型层级是 "
            f"{[x.value for x in tier.typical_layers]}，此处在 {self.layer.value}"
        ]

    def validate(self) -> list[str]:
        """返回违规列表；空列表表示合规。汇总校验见 registry.validate_all()。

        覆盖 [a12] 第三~五章的全部硬规则：分区三规则、Bucket 五档、changelog 三选一、
        主键三原则、系统字段规范，外加 Paimon 自身的硬要求（主键列 NOT NULL、
        分区表主键含分区字段）。
        """
        problems: list[str] = []
        problems.extend(self._naming_problems())
        problems.extend(self._primary_key_problems())
        problems.extend(self._partition_problems())
        problems.extend(self._storage_option_problems())
        problems.extend(self._system_field_problems())
        return problems

    # -- 校验分解（每块对应原文的一条规则，便于定位） --

    def _naming_problems(self) -> list[str]:
        problems: list[str] = []
        for w in lint(self.name, expected_layer=self.layer):
            if "缺少可识别的数据域段" in w and self.name_omits_domain:
                continue  # 已登记的已知偏离
            if "建议带标准粒度后缀" in w and self.name_omits_suffix:
                continue  # 已登记的已知偏离（源文给定的表名）
            problems.append(f"命名: {w}")

        parsed = parse(self.name)

        # 豁免标记必须指向一条真实存在的偏离。表名改好了却留着标记，下一次改名时
        # 它会悄悄吞掉本该响的告警——「已登记的已知偏离」这套机制就从此不可信。
        if self.name_omits_domain and parsed.domain is not None:
            problems.append(
                f"命名: name_omits_domain=True 但表名解析出数据域段 {parsed.domain.name_cn}，"
                f"标记已失效请删除"
            )
        if self.name_omits_suffix and parsed.suffix is not None:
            problems.append(
                f"命名: name_omits_suffix=True 但表名带标准粒度后缀 _{parsed.suffix}，标记已失效请删除"
            )

        # 域段矛盾才是真违规：表名里解析出的域与注册表声明的域不一致
        if parsed.domain is not None and parsed.domain is not self.domain:
            problems.append(
                f"命名: 表名域段解析为 {parsed.domain.name_cn}，但注册表声明为 {self.domain.name_cn}"
            )
        return problems

    def _primary_key_problems(self) -> list[str]:
        """主键三原则 + Paimon 对主键列的硬要求。"""
        problems: list[str] = []
        cols = {c.name: c for c in self.all_columns()}

        if not self.primary_key:
            problems.append("缺少主键（主键三原则一：业务主键优先）")
        if len(set(self.primary_key)) != len(self.primary_key):
            problems.append(f"主键字段重复: {self.primary_key}")

        system_names = set(SYSTEM_COLUMNS)
        for pk in self.primary_key:
            if pk not in cols:
                problems.append(f"主键字段 {pk!r} 不在字段列表中")
                continue
            # 原则一：业务主键优先，不用自增 ID
            if pk.lower() in NON_BUSINESS_PK_NAMES:
                problems.append(
                    f"主键字段 {pk!r} 没有业务含义（原则一：业务主键优先，不用自增 ID）"
                )
            # 系统字段是入湖元数据，不表达业务粒度，不能当主键
            if pk in system_names:
                problems.append(f"系统字段 {pk!r} 不得作为主键（原则一：业务主键优先）")
            # Paimon 硬要求：主键列必须 NOT NULL
            if cols[pk].nullable:
                problems.append(f"主键字段 {pk!r} 必须 NOT NULL（Paimon 对主键列的硬要求）")
        return problems

    def _partition_problems(self) -> list[str]:
        """分区决策三规则 + 原则三（分区表主键必须含分区字段）。"""
        problems: list[str] = []
        cols = {c.name: c for c in self.all_columns()}

        try:
            partition_rule_for(self.partition_by)
        except ValueError as exc:
            problems.append(f"分区: {exc}")

        for part in self.partition_by:
            if part not in cols:
                problems.append(f"分区字段 {part!r} 不在字段列表中")
            elif cols[part].nullable:
                problems.append(f"分区字段 {part!r} 必须 NOT NULL（Paimon 对分区列的硬要求）")
            # 原则三：分区表主键必须含分区字段（Paimon 硬要求）
            if part not in self.primary_key:
                problems.append(
                    f"分区表主键必须包含分区字段 {part!r}（Paimon 要求），当前 PK={self.primary_key}"
                )
        return problems

    def _storage_option_problems(self) -> list[str]:
        """Bucket 五档 + changelog 三选一 + WITH 子句保留 key。"""
        problems: list[str] = []

        if self.bucket not in BUCKET_TIERS:
            problems.append(f"Bucket {self.bucket} 不在五档 {sorted(BUCKET_TIERS)} 内")

        expected_cl = ChangelogProducer.default_for(self.layer)
        if self.changelog_producer is not expected_cl:
            problems.append(
                f"changelog-producer={self.changelog_producer.value} 偏离 {self.layer.value} 层默认 "
                f"{expected_cl.value}（如为有意选择，请在 notes 说明）"
            )

        # extra_options 覆盖 bucket/changelog-producer 会让上面两条校验形同虚设：
        # validate() 看的是字段，render_ddl() 写进 SQL 的却是 options。
        for key in sorted(set(self.extra_options) & RESERVED_TABLE_OPTIONS):
            problems.append(
                f"extra_options 不得覆盖保留项 {key!r}——它由 TableSpec 专有字段渲染，"
                f"从 options 旁路会绕开 Bucket 五档 / changelog 三选一的硬校验"
            )
        return problems

    def _system_field_problems(self) -> list[str]:
        """系统字段规范：两组字段按层挂载，不许串层，且形状必须与规范一致。"""
        problems: list[str] = []
        columns = self.all_columns()
        names = {c.name for c in columns}

        expected, _purpose = SYSTEM_FIELD_SPEC[self.layer]
        for fname in expected:
            if fname not in names:
                problems.append(f"缺少 {self.layer.value} 层系统字段 {fname!r}")

        # 只按名字查「在不在」是不够的：all_columns() 是「业务字段优先」——
        # 某张表若自己声明了一个同名业务列，系统字段就不会再被追加，而名字检查照样通过，
        # 于是 DDL 里挂出去的是业务列的类型（比如 update_time STRING），
        # 系统字段规范在类型这一维被静默架空。这里把类型与可空性一并对齐。
        for col in columns:
            spec_col = SYSTEM_COLUMNS.get(col.name)
            if spec_col is None:
                continue
            if col.type.upper() != spec_col.type.upper():
                problems.append(
                    f"系统字段 {col.name!r} 类型应为 {spec_col.type}，实际 {col.type}"
                    "（业务列不得以同名覆盖系统字段的类型）"
                )
            if col.nullable != spec_col.nullable:
                want = "NOT NULL" if not spec_col.nullable else "可空"
                problems.append(
                    f"系统字段 {col.name!r} 可空性应为 {want}"
                    f"（{'NOT NULL' if not col.nullable else '可空'} ≠ 规范）"
                )

        # 反向：ODS 层用 _source_system「换掉」update_time，两组不许混挂
        forbidden = set(SYSTEM_COLUMNS) - set(expected)
        for fname in sorted(forbidden & names):
            problems.append(
                f"{self.layer.value} 层不得出现 {fname!r}（系统字段规范：{self.layer.value} 层为 "
                f"{' + '.join(expected)}）"
            )

        if self.layer is Layer.ODS and not self.source_system:
            problems.append("ODS 表必须声明 source_system（_source_system 字段的取值来源）")
        return problems

    # ---- 渲染 ----

    def render_ddl(
        self,
        *,
        catalog: str = "paimon",
        database: str = "adas_lakehouse",
        variant_fallback_type: str | None = None,
    ) -> str:
        """渲染 Flink SQL 建表语句。

        主键一律 ``NOT ENFORCED``：Paimon 不在写入时强制校验唯一性（由上游保证），
        但会基于主键做 Upsert 合并——这对 CDC 实时入湖场景至关重要（[a10] 第五章①）。

        :param variant_fallback_type: 默认 None = 不降级，产物与从前一致。
            见 :meth:`Column.render`。
        """
        cols = [c.render(variant_fallback_type=variant_fallback_type) for c in self.all_columns()]
        pk = ", ".join(f"`{k}`" for k in self.primary_key)
        cols.append(f"  PRIMARY KEY ({pk}) NOT ENFORCED")

        opts: dict[str, str] = {
            "bucket": str(self.bucket),
            "changelog-producer": self.changelog_producer.value,
        }
        # 保留项由上面两行负责；extra_options 越权会在 validate() 报违规，
        # 渲染这里同样不让它覆盖，避免「校验说合规、DDL 却不是那回事」。
        opts.update(
            {k: v for k, v in self.extra_options.items() if k not in RESERVED_TABLE_OPTIONS}
        )
        opt_sql = ",\n".join(f"  {sql_literal(k)} = {sql_literal(v)}" for k, v in opts.items())

        part_sql = ""
        if self.partition_by:
            part_sql = "PARTITIONED BY ({})\n".format(
                ", ".join(f"`{p}`" for p in self.partition_by)
            )

        header = (
            f"-- {self.name}  [{self.domain.name_cn} / {self.layer.value.upper()}]  {self.comment}"
        )
        if self.source_system:
            header += f"\n-- 来源系统: {self.source_system}"
        if self.notes:
            header += f"\n-- 备注: {self.notes}"
        if self.fields_inferred:
            header += "\n-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL"

        return (
            f"{header}\n"
            f"CREATE TABLE IF NOT EXISTS `{catalog}`.`{database}`.`{self.name}` (\n"
            + ",\n".join(cols)
            + "\n) "
            + part_sql
            + "WITH (\n"
            + opt_sql
            + "\n);\n"
        )
