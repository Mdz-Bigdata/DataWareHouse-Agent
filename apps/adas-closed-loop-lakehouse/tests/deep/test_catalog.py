"""catalog 深度对账：把 [a12] 第三~五章的四张决策表逐字钉死在代码里。

与 ``tests/test_catalog_registry.py`` 的分工：
  · 那份测的是「不出圈」——88 张表的 bucket 落在五档内、changelog 合层、分区表主键含分区字段。
  · 这份测的是「逐字」——原文点名的取值一个字都不许改：五档的适用场景/典型层级/代表表、
    三选一的适用场景/原理/口诀/选错后果、分区三规则标题与分区全景 6 行、
    主键三原则的三个示例主键、系统字段两组的用途表述。

判据来源（均为原文原话，标注章节以便回查）：
  [a12] = 系列二第 2 篇《数仓命名规范 + 11 数据域划分 + 分区策略全解》
  [a10] = 系列二第 1 篇《Apache Paimon 分层建模实践》
  [a13] = 系列二第 3 篇（DDL 节选，与 [a12] 冲突项见 docs/source-deviations.md A-4 / A-4-1）
"""

from __future__ import annotations

import dataclasses

import pytest

from adas_lakehouse.catalog import registry
from adas_lakehouse.catalog.spec import (
    BUCKET_TIERS,
    CHANGELOG_DECISION_MNEMONIC,
    CHANGELOG_MISCHOICE_CONSEQUENCE,
    CHANGELOG_MODES,
    DATE_PARTITION_FIELD,
    NON_BUSINESS_PK_NAMES,
    PARTITION_PANORAMA,
    PARTITION_RULES,
    PRIMARY_KEY_PRINCIPLES,
    RESERVED_TABLE_OPTIONS,
    SYSTEM_FIELD_SPEC,
    ChangelogProducer,
    Column,
    PartitionRule,
    TableSpec,
    partition_rule_for,
    sql_literal,
)
from adas_lakehouse.domains import DataDomain, Layer

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def tables() -> tuple[TableSpec, ...]:
    return registry.all_tables()


def _spec(**over) -> TableSpec:
    """一张最小合法表，用于负例——不碰注册表里的真表。"""
    base: dict = {
        "name": "dwd_collect_probe_detail",
        "layer": Layer.DWD,
        "domain": DataDomain.COLLECT,
        "comment": "负例探针",
        "columns": [
            Column("data_id", "STRING", "锚点", nullable=False),
            Column("dt", "STRING", "日期分区", nullable=False),
        ],
        "primary_key": ("data_id",),
        "bucket": 4,
    }
    base.update(over)
    return TableSpec(**base)


# =========================================================================== Bucket 五档
# [a12] 第四章「Bucket 五档决策表」：档位 / 适用场景 / 典型层级 / 代表表


#: 原文五行，逐字。改这张表 = 改原文，不是改测试。
A12_BUCKET_TABLE = {
    1: ("字典表/极小表", (Layer.ADS,), ("ads_storage_cost_dashboard",)),
    2: ("DWS 汇总表 / 小 ADS", (Layer.DWS, Layer.ADS), ("dws_production_efficiency_daily",)),
    4: (
        "中等体量 ODS/DWD",
        (Layer.ODS, Layer.DWD),
        ("ods_collect_task", "dwd_training_task_detail"),
    ),
    8: ("大体量明细表", (Layer.DWD,), ("dwd_production_execution_detail",)),
    16: (
        "超大表 / 高并发写入",
        (Layer.DWD,),
        ("dwd_data_production_chain", "dwd_mining_image_vector_detail"),
    ),
}


def test_bucket_tiers_are_exactly_the_five_from_the_article():
    assert sorted(BUCKET_TIERS) == [1, 2, 4, 8, 16]


@pytest.mark.parametrize("bucket", sorted(A12_BUCKET_TABLE))
def test_each_bucket_tier_matches_the_article_verbatim(bucket):
    scenario, layers, examples = A12_BUCKET_TABLE[bucket]
    tier = BUCKET_TIERS[bucket]
    assert tier.bucket == bucket
    assert tier.scenario == scenario
    assert tier.typical_layers == layers
    assert tier.example_tables == examples


@pytest.mark.parametrize("bucket", sorted(A12_BUCKET_TABLE))
def test_article_named_example_tables_really_carry_that_bucket(bucket):
    """原文点名「代表表」的那几张，注册表里必须真取那一档。"""
    for name in A12_BUCKET_TABLE[bucket][2]:
        assert registry.by_name(name).bucket == bucket


def test_no_table_uses_a_bucket_outside_the_five_tiers(tables):
    assert {t.bucket for t in tables} == {1, 2, 4, 8, 16}


def test_bucket_distribution_is_pinned(tables):
    """当前五档分布。变动说明有表改了档位——改得对就改这里，别删断言。"""
    dist = {b: len(names) for b, names in registry.bucket_distribution().items()}
    assert dist == {1: 2, 2: 24, 4: 44, 8: 10, 16: 8}
    assert sum(dist.values()) == len(tables) == 88


def test_bucket_32_is_rejected_because_the_project_took_the_five_tier_table():
    """[a10] 第五章②「DWD 层核心表 16-32 个 bucket」与 [a13] 的 'bucket'='32' 均未采用。

    偏差已登记 source-deviations A-4：五档是封闭集合，可硬校验；区间式描述不可校验。
    """
    assert 32 not in BUCKET_TIERS
    assert _spec(bucket=32).validate() == [f"Bucket 32 不在五档 {[1, 2, 4, 8, 16]} 内"]
    # [a10] / [a13] 点名取 32 的那张表，本项目取 16
    assert registry.by_name("dwd_production_artifact_detail").bucket == 16


def test_only_the_documented_table_deviates_from_its_tier_typical_layer(tables):
    """「典型层级」是惯例不是红线，但偏离的表必须是已知那一张。"""
    deviating = {t.name for t in tables if t.bucket_notes()}
    assert deviating == {"dwd_mining_tag_dict_detail"}
    assert registry.by_name("dwd_mining_tag_dict_detail").bucket == 1

    # 同一结论必须能从注册表的非阻断审计口子拿到（不参与 make check 的退出码）
    audit = registry.audit_notes()
    assert {n for n, v in audit.items() if v["bucket"]} == {"dwd_mining_tag_dict_detail"}
    assert registry.validate_all() == {}, "典型层级偏离不该升级成硬违规"


def test_a10_named_bucket_examples_hold():
    """[a10] 第五章②举例：dwd_collect_clip_detail 设 16 个 bucket。"""
    assert registry.by_name("dwd_collect_clip_detail").bucket == 16


# ================================================================= changelog-producer 三选一
# [a12] 第四章「changelog-producer 三选一」：模式 / 适用场景 / 原理


#: 原文三行，逐字。
A12_CHANGELOG_TABLE = {
    "input": (
        "ODS CDC 透传 / Append-only 明细",
        "上游写入本身就是完整 changelog，直接透传，零额外开销",
        (Layer.ODS,),
    ),
    "lookup": (
        "DWD Upsert 表（需对比旧值）",
        "写入时 lookup 旧值产生 -U/+U 变更，适合状态频繁更新的链路表",
        (Layer.DWD,),
    ),
    "full-compaction": (
        "DWS/ADS 离线聚合",
        "在 full-compaction 时产生 changelog，适合批量写入、低频更新的聚合表",
        (Layer.DWS, Layer.ADS),
    ),
}


def test_changelog_producer_has_exactly_three_options():
    assert [p.value for p in ChangelogProducer] == ["input", "lookup", "full-compaction"]
    assert set(CHANGELOG_MODES) == set(ChangelogProducer)


@pytest.mark.parametrize("value", sorted(A12_CHANGELOG_TABLE))
def test_each_changelog_mode_matches_the_article_verbatim(value):
    scenario, principle, layers = A12_CHANGELOG_TABLE[value]
    mode = CHANGELOG_MODES[ChangelogProducer(value)]
    assert mode.scenario == scenario
    assert mode.principle == principle
    assert mode.layers == layers


def test_decision_mnemonic_is_the_article_sentence():
    assert CHANGELOG_DECISION_MNEMONIC == (
        "ODS 从 CDC 来 → input；DWD 有 Upsert → lookup；DWS/ADS 批量聚合 → full-compaction"
    )


def test_the_consequence_of_choosing_wrong_is_recorded():
    """原文点名的后果——这句话是 changelog 做成硬校验而非风格建议的全部理由。"""
    assert CHANGELOG_MISCHOICE_CONSEQUENCE == (
        "选错最直接的后果：该用 lookup 的表用了 input，下游拿到的 changelog 缺少 -U 记录，"
        "增量同步数据不一致。"
    )


def test_changelog_distribution_is_pinned_and_layer_pure(tables):
    dist = registry.changelog_distribution()
    assert {p.value: len(n) for p, n in dist.items()} == {
        "input": 33,
        "lookup": 30,
        "full-compaction": 25,
    }
    for producer, names in dist.items():
        allowed = CHANGELOG_MODES[producer].layers
        assert {registry.by_name(n).layer for n in names} <= set(allowed)


def test_dwd_table_with_input_producer_is_a_violation():
    """A-4-1：[a13] 的 DDL 节选给 DWD 表写 input，本项目按 [a12] 口诀判违规。"""
    problems = _spec(changelog_producer=ChangelogProducer.INPUT).validate()
    assert problems == [
        "changelog-producer=input 偏离 dwd 层默认 lookup（如为有意选择，请在 notes 说明）"
    ]


def test_a4_1_conflict_is_registered_in_source_deviations(repo_root):
    """[a13] 3.1 的 'changelog-producer'='input' 与 [a12] 口诀的冲突必须有登记。"""
    text = (repo_root / "docs" / "source-deviations.md").read_text(encoding="utf-8")
    assert "#### A-4-1" in text
    section = text.split("#### A-4-1", 1)[1].split("\n### ", 1)[0]
    assert "changelog-producer" in section
    assert "input" in section and "lookup" in section
    assert "dwd_production_artifact_detail" in section


# ======================================================================== 分区决策三规则
# [a12] 第三章「分区决策三规则」+「分区全景表」


def test_three_partition_rules_are_titled_verbatim():
    titles = [PARTITION_RULES[r].title for r in PartitionRule]
    assert titles == [
        "规则一：大体量 + 时间范围查询 → 按 dt 分区",
        "规则二：有明确业务分类过滤 → 按业务字段分区",
        "规则三：主键 Upsert + 无明确分区维度 → 不分区",
    ]


def test_rule_details_name_the_article_example_tables():
    assert (
        "dwd_mining_image_vector_detail（千万~亿级）"
        in PARTITION_RULES[PartitionRule.BY_DATE].detail
    )
    rule2 = PARTITION_RULES[PartitionRule.BY_BUSINESS_FIELD].detail
    assert "ods_vehicle_trigger_event 按 trigger_type" in rule2
    assert "dwd_evaluation_result_detail 按 evaluation_type" in rule2
    assert "绝大多数 DWD/DWS/ADS 表属于此类" in PARTITION_RULES[PartitionRule.NO_PARTITION].detail


def test_partition_rule_classifier():
    assert partition_rule_for(()) is PartitionRule.NO_PARTITION
    assert partition_rule_for(("dt",)) is PartitionRule.BY_DATE
    assert partition_rule_for(("trigger_type",)) is PartitionRule.BY_BUSINESS_FIELD
    assert DATE_PARTITION_FIELD == "dt"


def test_multi_level_partition_is_outside_the_three_rules():
    """[a5] 第四章的「dt 主分区 + 维度辅分区」未采用（source-deviations A-5）。"""
    with pytest.raises(ValueError, match="多级分区"):
        partition_rule_for(("dt", "trigger_type"))
    problems = _spec(partition_by=("dt", "data_id"), primary_key=("data_id", "dt")).validate()
    assert any("多级分区" in p for p in problems)


#: [a12] 第三章「分区全景表」六行，逐字：表名 / 分区字段 / 类型 / 原因
A12_PARTITION_PANORAMA = [
    ("dwd_mining_image_vector_detail", "dt", "日期", "全湖最大表，按天降冷+索引刷新"),
    ("ods_quality_issue", "dt", "日期", "异常隔离表，按天 TTL 清理"),
    ("ods_vehicle_trigger_event", "trigger_type", "业务字段", "按触发类型过滤"),
    ("dwd_evaluation_result_detail", "evaluation_type", "业务字段", "离线/仿真/实车差异大"),
    ("ods_data_file_meta", "file_type", "业务字段", "文件类型差异大"),
    ("ods_production_kafka_event", "event_type", "业务字段", "事件类型数据量差异大"),
]


def test_partition_panorama_is_the_article_table_verbatim():
    got = [(e.table, e.field, e.kind, e.reason) for e in PARTITION_PANORAMA]
    assert got == A12_PARTITION_PANORAMA


@pytest.mark.parametrize(("name", "field", "kind", "_reason"), A12_PARTITION_PANORAMA)
def test_each_panorama_row_matches_the_registry(name, field, kind, _reason):
    spec = registry.by_name(name)
    assert spec.partition_by == (field,)
    expected = PartitionRule.BY_DATE if kind == "日期" else PartitionRule.BY_BUSINESS_FIELD
    assert spec.partition_rule is expected
    # 原则三：分区字段必须在主键里
    assert field in spec.primary_key


def test_exactly_six_partitioned_tables_and_the_rest_follow_rule_three(tables):
    dist = registry.partition_distribution()
    assert len(dist[PartitionRule.BY_DATE]) == 2
    assert len(dist[PartitionRule.BY_BUSINESS_FIELD]) == 4
    assert len(dist[PartitionRule.NO_PARTITION]) == 88 - 6
    parted = {t.name for t in tables if t.partition_by}
    assert parted == {row[0] for row in A12_PARTITION_PANORAMA}


def test_a10_claim_of_a_single_partitioned_table_is_not_what_we_implement():
    """[a10] 第三章③说向量表是「全湖唯一的分区表」；本项目取 [a12] 的 6 张（A-5）。"""
    assert len(PARTITION_PANORAMA) == 6
    assert registry.by_name("dwd_mining_image_vector_detail").partition_by == ("dt",)


def test_partition_column_must_be_not_null():
    problems = _spec(
        partition_by=("dt",),
        primary_key=("data_id", "dt"),
        columns=[
            Column("data_id", "STRING", "锚点", nullable=False),
            Column("dt", "STRING", "日期分区"),  # nullable
        ],
    ).validate()
    assert any("分区字段 'dt' 必须 NOT NULL" in p for p in problems)


# ========================================================================== 主键三原则
# [a12] 第五章「主键设计三原则」


def test_three_primary_key_principles_are_titled_verbatim():
    assert [p.title for p in PRIMARY_KEY_PRINCIPLES] == [
        "原则一：业务主键优先",
        "原则二：复合主键表达完整粒度",
        "原则三：分区表主键必须含分区字段",
    ]
    assert [p.ordinal for p in PRIMARY_KEY_PRINCIPLES] == [1, 2, 3]


def test_principle_details_quote_the_article():
    p1, p2, p3 = PRIMARY_KEY_PRINCIPLES
    assert "用业务含义明确的字段做主键，不用自增 ID" in p1.detail
    assert "(dataset_id, version)——同一个数据集可以有多个版本" in p2.detail
    assert "PK 为 (image_id, embedding_version, dt)" in p3.detail


def test_article_named_primary_keys_are_verbatim_including_order():
    """原文点名的三组 PK，字段与顺序都不许动。"""
    assert registry.by_name("dwd_data_production_chain").primary_key == ("data_id",)
    assert registry.by_name("dwd_training_task_detail").primary_key == ("training_task_id",)
    assert registry.by_name("dwd_dataset_version_detail").primary_key == ("dataset_id", "version")
    assert registry.by_name("dwd_mining_image_vector_detail").primary_key == (
        "image_id",
        "embedding_version",
        "dt",
    )
    # [a10] 第五章①：ods_collect_task 以 collect_task_id 为主键
    assert registry.by_name("ods_collect_task").primary_key == ("collect_task_id",)


def test_principle_one_rejects_a_meaningless_surrogate_key():
    """「不用自增 ID」落成黑名单硬校验。"""
    assert "id" in NON_BUSINESS_PK_NAMES
    problems = _spec(
        columns=[Column("id", "BIGINT", "自增主键", nullable=False)],
        primary_key=("id",),
    ).validate()
    assert any("没有业务含义" in p and "原则一" in p for p in problems)


def test_no_table_in_the_lake_uses_a_meaningless_surrogate_key(tables):
    for t in tables:
        for pk in t.primary_key:
            assert pk.lower() not in NON_BUSINESS_PK_NAMES, f"{t.name} 主键 {pk!r} 无业务含义"


def test_principle_three_is_a_hard_gate():
    problems = _spec(partition_by=("dt",), primary_key=("data_id",)).validate()
    assert any("分区表主键必须包含分区字段 'dt'" in p for p in problems)


def test_every_primary_key_column_is_not_null(tables):
    """Paimon 对主键列的硬要求。缺了它写入期才炸，建表期就该拦住。"""
    for t in tables:
        cols = {c.name: c for c in t.all_columns()}
        for pk in t.primary_key:
            assert not cols[pk].nullable, f"{t.name} 主键 {pk!r} 可空"


def test_system_field_cannot_be_a_primary_key():
    problems = _spec(primary_key=("_ingest_time",)).validate()
    assert any("系统字段 '_ingest_time' 不得作为主键" in p for p in problems)


def test_composite_primary_keys_actually_exist_for_principle_two(tables):
    """原则二不是纸面原则：全湖确实有大量复合主键表。"""
    composite = [t.name for t in tables if len(t.primary_key) > 1]
    assert len(composite) >= 30
    assert "dwd_dataset_version_detail" in composite


# ======================================================================== 系统字段规范
# [a12] 第五章「系统字段规范」


def test_system_field_spec_matches_the_article_two_rows():
    assert SYSTEM_FIELD_SPEC[Layer.ODS] == (
        ("_ingest_time", "_source_system"),
        "入湖时间 + 来源系统标识，支持多源追溯",
    )
    for layer in (Layer.DWD, Layer.DWS, Layer.ADS):
        assert SYSTEM_FIELD_SPEC[layer] == (
            ("_ingest_time", "update_time"),
            "入湖时间 + 业务更新时间，支持增量同步与变更追踪",
        )


def test_ods_swaps_update_time_for_source_system(tables):
    """原文：ODS 层用 _source_system「换掉」update_time——是替换，不是并存。"""
    for t in tables:
        names = {c.name for c in t.all_columns()}
        assert "_ingest_time" in names
        if t.layer is Layer.ODS:
            assert "_source_system" in names
            assert "update_time" not in names, f"{t.name} 不该有 update_time"
        else:
            assert "update_time" in names
            assert "_source_system" not in names, f"{t.name} 不该有 _source_system"


def test_mixing_the_two_system_field_groups_is_a_violation():
    problems = _spec(
        layer=Layer.ODS,
        name="ods_collect_probe",
        source_system="探针",
        columns=[
            Column("data_id", "STRING", "锚点", nullable=False),
            Column("update_time", "TIMESTAMP(3)", "业务更新时间"),
        ],
    ).validate()
    assert any("ods 层不得出现 'update_time'" in p for p in problems)


def test_ods_must_declare_where_source_system_comes_from():
    problems = _spec(layer=Layer.ODS, name="ods_collect_probe", source_system="").validate()
    assert any("必须声明 source_system" in p for p in problems)


# ============================================================================ DDL 渲染


def test_not_enforced_primary_key_on_every_table(tables):
    """[a10] 第五章①：业务主键 + NOT ENFORCED，由 Paimon 按主键做 Upsert 合并。"""
    for t in tables:
        assert "NOT ENFORCED" in t.render_ddl()


def test_single_quotes_in_comments_are_escaped():
    """列注释里真的有单引号（CONCAT(run_id, '_', data_id)），不转义就产出语法非法的 DDL。"""
    assert sql_literal("a'b") == "'a''b'"
    ddl = registry.by_name("dwd_mining_result_detail").render_ddl()
    assert "CONCAT(run_id, ''_'', data_id)" in ddl


def test_every_ddl_body_line_has_balanced_quotes(tables):
    """建表体内每一行的单引号必须成对——奇数即字符串提前截断。"""
    for t in tables:
        body = t.render_ddl().split("(\n", 1)[1]
        for line in body.splitlines():
            if line.startswith("--"):
                continue  # 注释行不参与 SQL 解析
            assert line.count("'") % 2 == 0, f"{t.name} 该行引号不成对: {line}"


def test_extra_options_cannot_override_bucket_or_changelog_producer():
    """否则 validate() 看字段说合规，render_ddl() 写进 SQL 的却是另一回事。"""
    assert sorted(RESERVED_TABLE_OPTIONS) == [
        "bucket",
        "changelog-producer",
        "partition",
        "primary-key",
    ]
    spec = _spec(extra_options={"bucket": "32", "changelog-producer": "input"})
    problems = spec.validate()
    assert sum("extra_options 不得覆盖保留项" in p for p in problems) == 2
    ddl = spec.render_ddl()
    assert "'bucket' = '4'" in ddl and "'bucket' = '32'" not in ddl
    assert "'changelog-producer' = 'lookup'" in ddl


def test_legitimate_extra_options_still_render():
    ddl = registry.by_name("dwd_mining_image_vector_detail").render_ddl()
    assert "'file.format' = 'parquet'" in ddl
    assert "'bucket' = '16'" in ddl
    assert "'changelog-producer' = 'lookup'" in ddl
    assert "PARTITIONED BY (`dt`)" in ddl


# ==================================================================== 全湖反向对账


def test_source_matrices_reconcile_clean():
    assert registry.reconcile_source_matrices() == []
    assert registry.validate_all() == {}


@pytest.mark.parametrize(
    ("name", "change", "needle"),
    [
        ("dwd_data_production_chain", {"bucket": 8}, "Bucket 五档"),
        ("dwd_dataset_version_detail", {"primary_key": ("dataset_id",)}, "主键三原则"),
        ("ods_vehicle_trigger_event", {"partition_by": ()}, "分区全景"),
    ],
)
def test_reconciliation_catches_drift_on_article_named_values(monkeypatch, name, change, needle):
    """把原文点名的取值改掉，反向对账必须响——否则这些常量就是摆设。"""
    original = registry.all_tables()
    drifted = tuple(dataclasses.replace(t, **change) if t.name == name else t for t in original)
    monkeypatch.setattr(registry, "all_tables", lambda: drifted)

    problems = registry.reconcile_source_matrices()
    assert any(needle in p for p in problems), problems
    assert registry.SOURCE_MATRIX_KEY in registry.validate_all()


def test_validate_all_can_be_narrowed_to_per_table():
    assert registry.validate_all(include_source_matrices=False) == {}
