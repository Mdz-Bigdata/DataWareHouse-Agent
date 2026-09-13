"""全湖表注册表：88 张表的硬约束。

这些断言就是湖仓的「地基验收单」——任何一条挂掉都意味着新增/修改表定义时
踩破了物理存储策略的红线，不是测试写错了。

约束来自 catalog/spec.py 模块 docstring（系列二第三~五章）：
  · 分区决策三规则（大体量按 dt / 有业务分类按业务字段 / Upsert 无维度则不分区）
  · Bucket 五档（1 / 2 / 4 / 8 / 16）
  · changelog-producer 三选一：ODS input / DWD lookup / DWS·ADS full-compaction
  · 主键三原则（业务主键优先 / 复合主键表达粒度 / 分区表主键必须含分区字段）
  · 系统字段规范（ODS: _ingest_time + _source_system；其余: _ingest_time + update_time）
"""

from __future__ import annotations

from collections import Counter

import pytest

from adas_lakehouse.catalog import registry
from adas_lakehouse.catalog.spec import BUCKET_TIERS, ChangelogProducer, TableSpec
from adas_lakehouse.domains import DataDomain, Layer

pytestmark = pytest.mark.contract

#: 全湖表数。原文三处口径互不自洽，本项目以「显式表名清单」为准并补登记 1 张。
TOTAL_TABLES = 88

#: 分层表数（含质量门禁伪域的 1 张 ODS 表）。
LAYER_COUNTS = {Layer.ODS: 33, Layer.DWD: 30, Layer.DWS: 14, Layer.ADS: 11}

#: 分区表恰好 6 张——其余全部走「Upsert 无维度则不分区」。
PARTITIONED_TABLES = {
    "ods_data_file_meta",
    "ods_production_kafka_event",
    "ods_quality_issue",
    "ods_vehicle_trigger_event",
    "dwd_evaluation_result_detail",
    "dwd_mining_image_vector_detail",
}


@pytest.fixture(scope="module")
def tables() -> tuple[TableSpec, ...]:
    return registry.all_tables()


# --------------------------------------------------------------------------- 总量与分层


def test_total_table_count(tables):
    assert len(tables) == TOTAL_TABLES


def test_layer_counts(tables):
    got = Counter(t.layer for t in tables)
    assert dict(got) == LAYER_COUNTS
    assert sum(LAYER_COUNTS.values()) == TOTAL_TABLES


def test_table_names_are_unique(tables):
    names = [t.name for t in tables]
    dupes = [n for n, c in Counter(names).items() if c > 1]
    assert dupes == [], f"表名重复: {dupes}"
    assert len(set(names)) == TOTAL_TABLES


def test_tables_are_sorted_by_layer_then_domain_then_name(tables):
    order = {layer: i for i, layer in enumerate(Layer)}
    keys = [(order[t.layer], t.domain.ordinal, t.name) for t in tables]
    assert keys == sorted(keys)


def test_validate_all_is_empty(tables):
    """零违规。有内容说明某张表踩破了 spec.validate() 的硬校验。"""
    problems = registry.validate_all()
    assert problems == {}, f"存在违规表: {problems}"


def test_by_name_and_by_layer_and_by_domain_agree(tables):
    for t in tables:
        assert registry.by_name(t.name) is t
    with pytest.raises(KeyError):
        registry.by_name("dwd_not_a_real_table")

    assert sum(len(registry.by_layer(lyr)) for lyr in Layer) == TOTAL_TABLES
    assert sum(len(registry.by_domain(d)) for d in DataDomain) == TOTAL_TABLES


# --------------------------------------------------------------------------- 分区策略


def test_exactly_six_partitioned_tables(tables):
    parted = {t.name for t in tables if t.partition_by}
    assert parted == PARTITIONED_TABLES
    assert len(parted) == 6


def test_partition_fields_are_all_inside_primary_key(tables):
    """主键三原则之三：分区表主键必须包含分区字段（Paimon 硬要求）。"""
    for t in tables:
        for part in t.partition_by:
            assert part in t.primary_key, f"{t.name} 分区字段 {part!r} 不在主键 {t.primary_key} 内"


def test_partition_fields_exist_as_columns(tables):
    for t in tables:
        names = {c.name for c in t.all_columns()}
        for part in t.partition_by:
            assert part in names, f"{t.name} 分区字段 {part!r} 没有对应字段定义"


def test_unpartitioned_tables_really_have_no_partition_clause(tables):
    """反向断言：除这 6 张外，其余表渲染出的 DDL 不得出现 PARTITIONED BY。"""
    for t in tables:
        if t.name in PARTITIONED_TABLES:
            continue
        assert "PARTITIONED BY" not in t.render_ddl(), f"{t.name} 不该带分区子句"


# --------------------------------------------------------------------------- bucket 五档


def test_bucket_values_are_limited_to_five_tiers(tables):
    assert set(BUCKET_TIERS) == {1, 2, 4, 8, 16}
    used = {t.bucket for t in tables}
    assert used <= {1, 2, 4, 8, 16}, f"出现五档之外的 bucket: {used - {1, 2, 4, 8, 16}}"


def test_every_bucket_tier_documents_its_scenario():
    for tier, scenario in BUCKET_TIERS.items():
        assert isinstance(tier, int) and scenario


# --------------------------------------------------------------------------- changelog-producer


def test_changelog_producer_matches_layer(tables):
    """决策口诀：ODS input / DWD lookup / DWS·ADS full-compaction。

    该用 lookup 的表用了 input，下游拿到的 changelog 缺少 -U 记录，
    增量同步就会数据不一致——所以这条是硬约束，不是风格偏好。
    """
    expected = {
        Layer.ODS: ChangelogProducer.INPUT,
        Layer.DWD: ChangelogProducer.LOOKUP,
        Layer.DWS: ChangelogProducer.FULL_COMPACTION,
        Layer.ADS: ChangelogProducer.FULL_COMPACTION,
    }
    for t in tables:
        assert t.changelog_producer is expected[t.layer], (
            f"{t.name}（{t.layer.value}）的 changelog-producer 是 "
            f"{t.changelog_producer.value}，应为 {expected[t.layer].value}"
        )


def test_changelog_default_for_helper_agrees():
    assert ChangelogProducer.default_for(Layer.ODS) is ChangelogProducer.INPUT
    assert ChangelogProducer.default_for(Layer.DWD) is ChangelogProducer.LOOKUP
    assert ChangelogProducer.default_for(Layer.DWS) is ChangelogProducer.FULL_COMPACTION
    assert ChangelogProducer.default_for(Layer.ADS) is ChangelogProducer.FULL_COMPACTION


# --------------------------------------------------------------------------- 主键与字段


def test_every_table_has_a_primary_key_backed_by_real_columns(tables):
    for t in tables:
        assert t.primary_key, f"{t.name} 缺少主键"
        names = {c.name for c in t.all_columns()}
        for pk in t.primary_key:
            assert pk in names, f"{t.name} 主键字段 {pk!r} 不在字段列表中"
        assert len(set(t.primary_key)) == len(t.primary_key), f"{t.name} 主键字段重复"


def test_system_fields_are_appended_per_layer(tables):
    for t in tables:
        names = {c.name for c in t.all_columns()}
        for field in t.layer.system_fields:
            assert field in names, f"{t.name} 缺少系统字段 {field}"
        if t.layer is Layer.ODS:
            assert "_source_system" in names
        else:
            assert "update_time" in names


def test_ods_tables_declare_their_source_system(tables):
    """ODS 层 _source_system 的取值必须有来源声明，否则「数据从哪来」就丢了。"""
    for t in registry.by_layer(Layer.ODS):
        assert t.source_system, f"{t.name} 未声明 source_system"


def test_column_names_are_unique_within_each_table(tables):
    for t in tables:
        names = [c.name for c in t.all_columns()]
        dupes = [n for n, c in Counter(names).items() if c > 1]
        assert dupes == [], f"{t.name} 字段重复: {dupes}"


def test_every_table_has_a_comment(tables):
    for t in tables:
        assert t.comment, f"{t.name} 缺少表注释（表名即文档，注释是第二道）"


# --------------------------------------------------------------------------- DDL 渲染


def test_render_ddl_is_wellformed_for_every_table(tables):
    for t in tables:
        ddl = t.render_ddl()
        assert f"CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`{t.name}`" in ddl
        assert "PRIMARY KEY (" in ddl and "NOT ENFORCED" in ddl
        assert f"'bucket' = '{t.bucket}'" in ddl
        assert f"'changelog-producer' = '{t.changelog_producer.value}'" in ddl
        assert ddl.rstrip().endswith(");")


def test_render_ddl_honours_catalog_and_database_override():
    spec = registry.all_tables()[0]
    ddl = spec.render_ddl(catalog="pm", database="db")
    assert f"`pm`.`db`.`{spec.name}`" in ddl


def test_render_all_ddl_covers_every_table(tables):
    whole = registry.render_all_ddl()
    for t in tables:
        assert f"`{t.name}`" in whole
    for layer in Layer:
        chunk = registry.render_all_ddl(layer)
        assert chunk.count("CREATE TABLE IF NOT EXISTS") == LAYER_COUNTS[layer]


# --------------------------------------------------------------------------- 缓存语义


def test_all_tables_is_cached_and_returns_a_tuple():
    assert registry.all_tables() is registry.all_tables()
    assert isinstance(registry.all_tables(), tuple)
