"""全湖表目录：88 张 Paimon 表的唯一事实源。

分三层：

* ``spec``     —— 一张表的声明（``TableSpec``）与物理存储策略常量。
  [a12] 第三~五章的四张决策表在这里**逐字**落成常量：Bucket 五档
  （``BUCKET_TIERS``）、changelog-producer 三选一（``CHANGELOG_MODES``）、
  分区决策三规则 + 分区全景（``PARTITION_RULES`` / ``PARTITION_PANORAMA``）、
  主键三原则（``PRIMARY_KEY_PRINCIPLES``）、系统字段规范（``SYSTEM_FIELD_SPEC``）。
* ``tables``   —— 每个数据域一个模块，只放该域的 ``TableSpec`` 列表。
* ``registry`` —— 聚合、查询、逐表硬校验（``validate_all``）与原文决策表反向对账
  （``reconcile_source_matrices``）。

**表结构只在这里定义。** 子系统（ingest / mining / vector / lifecycle …）一律引用
不重复定义；需要新列就往 ``tables/_<domain>.py`` 里加，别在子系统本地另立一套。
"""

from .registry import (
    all_tables,
    audit_notes,
    bucket_distribution,
    by_domain,
    by_layer,
    by_name,
    changelog_distribution,
    counts,
    partition_distribution,
    reconcile_source_matrices,
    render_all_ddl,
    validate_all,
)
from .spec import (
    BUCKET_TIERS,
    CHANGELOG_DECISION_MNEMONIC,
    CHANGELOG_MISCHOICE_CONSEQUENCE,
    CHANGELOG_MODES,
    PARTITION_PANORAMA,
    PARTITION_RULES,
    PRIMARY_KEY_PRINCIPLES,
    SYSTEM_COLUMNS,
    SYSTEM_FIELD_SPEC,
    BucketTier,
    ChangelogMode,
    ChangelogProducer,
    Column,
    PartitionEntry,
    PartitionRule,
    PartitionRuleSpec,
    PrimaryKeyPrinciple,
    TableSpec,
    partition_rule_for,
)

__all__ = [
    "BUCKET_TIERS",
    "CHANGELOG_DECISION_MNEMONIC",
    "CHANGELOG_MISCHOICE_CONSEQUENCE",
    "CHANGELOG_MODES",
    "PARTITION_PANORAMA",
    "PARTITION_RULES",
    "PRIMARY_KEY_PRINCIPLES",
    "SYSTEM_COLUMNS",
    "SYSTEM_FIELD_SPEC",
    "BucketTier",
    "ChangelogMode",
    "ChangelogProducer",
    "Column",
    "PartitionEntry",
    "PartitionRule",
    "PartitionRuleSpec",
    "PrimaryKeyPrinciple",
    "TableSpec",
    "all_tables",
    "audit_notes",
    "bucket_distribution",
    "by_domain",
    "by_layer",
    "by_name",
    "changelog_distribution",
    "counts",
    "partition_distribution",
    "partition_rule_for",
    "reconcile_source_matrices",
    "render_all_ddl",
    "validate_all",
]
