"""11 数据域 × 四层 表数对账（伪域单列）。

原文三处口径互不自洽（见 catalog/registry.py 与 cli.py 的登记）：

  · 正文概述        79+ 张（ODS 28 / DWD 27）
  · 显式表名清单    87 张（ODS 32 / DWD 30 / DWS 14 / ADS 11）      ← 本项目以此为准
  · 11 数据域统计表 87+ 张（含质量门禁 1 张）

本项目 = 87 张显式清单 + 1 张补登记（ods_production_kafka_event，原文只在分区
全景表里出现过）= 88 张。质量门禁的 ods_quality_issue 不属于 11 数据域，作为
伪域单列——这样「11 数据域小计」才能直接跟原文统计表对账。

⚠️ 原文 11 数据域表只给出合计与分层数，未逐域给出表数。下面的逐域明细是本项目
的登记口径，对账在「分层」粒度成立；逐域断言起的是「防漂移」作用——
新增表时若忘了更新这张表，这里会立刻红。
"""

from __future__ import annotations

import pytest

from adas_lakehouse.catalog import registry
from adas_lakehouse.cli import (
    PROJECT_ADDED_TABLES,
    SOURCE_DOMAIN_TABLE_GATE_COUNT,
    SOURCE_EXPLICIT_COUNTS,
    SOURCE_PROSE_DWD,
    SOURCE_PROSE_ODS,
)
from adas_lakehouse.domains import QUALITY_GATE_PSEUDO_DOMAIN, DataDomain, Layer

pytestmark = pytest.mark.contract

#: 本项目登记口径：数据域 → {层: 表数}。缺的层表示该域在该层没有表。
REGISTERED_BY_DOMAIN: dict[DataDomain, dict[Layer, int]] = {
    DataDomain.COLLECT: {Layer.ODS: 4, Layer.DWD: 1},
    DataDomain.PRODUCTION: {Layer.ODS: 8, Layer.DWD: 5, Layer.DWS: 2, Layer.ADS: 1},
    DataDomain.DATASET: {Layer.ODS: 4, Layer.DWD: 3, Layer.DWS: 2, Layer.ADS: 2},
    DataDomain.TRAINING: {Layer.ODS: 3, Layer.DWD: 2, Layer.DWS: 1, Layer.ADS: 1},
    DataDomain.EVALUATION: {Layer.ODS: 3, Layer.DWD: 3, Layer.DWS: 2, Layer.ADS: 2},
    DataDomain.SIMULATION: {Layer.ODS: 2, Layer.DWD: 1},
    DataDomain.TRIGGER: {Layer.ODS: 3, Layer.DWD: 2, Layer.DWS: 1, Layer.ADS: 1},
    DataDomain.DEPLOYMENT: {Layer.ODS: 2, Layer.DWD: 2, Layer.DWS: 1, Layer.ADS: 1},
    DataDomain.ISSUE: {Layer.ODS: 1, Layer.DWD: 1},
    DataDomain.MINING: {Layer.ODS: 2, Layer.DWD: 8, Layer.DWS: 2, Layer.ADS: 1},
    DataDomain.CLOSED_LOOP: {Layer.DWD: 2, Layer.DWS: 3, Layer.ADS: 2},
}

#: 11 数据域小计（不含伪域）。
DOMAIN_SUBTOTAL = 87

#: 伪域（质量门禁隔离表）表数。
PSEUDO_TOTAL = 1


def _real_domain_tables():
    return [t for t in registry.all_tables() if not t.pseudo_domain]


# --------------------------------------------------------------------------- 数据域定义


def test_there_are_exactly_eleven_data_domains():
    """8 个闭环业务环节 + 3 个支撑域。"""
    assert len(list(DataDomain)) == 11


def test_domain_ordinals_are_one_through_eleven():
    assert sorted(d.ordinal for d in DataDomain) == list(range(1, 12))


def test_domain_prefixes_are_unique_and_wellformed():
    prefixes = [d.prefix for d in DataDomain]
    assert len(set(prefixes)) == 11
    for d in DataDomain:
        assert d.prefix.endswith("_")
        assert d.prefix == f"{d.key}_"
        assert d.name_cn and d.meaning


def test_quality_gate_is_a_pseudo_domain_not_one_of_the_eleven():
    assert QUALITY_GATE_PSEUDO_DOMAIN == "quality_"
    assert QUALITY_GATE_PSEUDO_DOMAIN.rstrip("_") not in {d.key for d in DataDomain}


# --------------------------------------------------------------------------- 逐域对账


@pytest.mark.parametrize("domain", list(DataDomain), ids=lambda d: d.key)
def test_table_count_per_domain_and_layer(domain):
    expected = REGISTERED_BY_DOMAIN[domain]
    got: dict[Layer, int] = {}
    for t in _real_domain_tables():
        if t.domain is domain:
            got[t.layer] = got.get(t.layer, 0) + 1
    assert got == expected, f"{domain.name_cn} 表数漂移: 期望 {expected}，实际 {got}"


def test_closed_loop_domain_has_no_ods_tables():
    """闭环域是跨域整合域，数据全部来自其他域 DWD 层加工——它不该有 ODS 表。"""
    ods = [t for t in registry.by_domain(DataDomain.CLOSED_LOOP) if t.layer is Layer.ODS]
    assert ods == []


def test_eleven_domain_subtotal():
    assert len(_real_domain_tables()) == DOMAIN_SUBTOTAL
    assert sum(sum(per.values()) for per in REGISTERED_BY_DOMAIN.values()) == DOMAIN_SUBTOTAL


def test_pseudo_domain_is_counted_separately():
    pseudo = [t for t in registry.all_tables() if t.pseudo_domain]
    assert len(pseudo) == PSEUDO_TOTAL
    assert {t.name for t in pseudo} == {"ods_quality_issue"}
    assert pseudo[0].pseudo_domain == QUALITY_GATE_PSEUDO_DOMAIN
    assert pseudo[0].layer is Layer.ODS


def test_domain_subtotal_plus_pseudo_equals_grand_total():
    assert DOMAIN_SUBTOTAL + PSEUDO_TOTAL == len(registry.all_tables()) == 88


# --------------------------------------------------------------------------- registry.counts()


def test_counts_matches_the_registered_table():
    """counts() 是给对账用的：伪域必须单列，键用中文域名。"""
    counts = registry.counts()
    pseudo_key = f"[伪域]{QUALITY_GATE_PSEUDO_DOMAIN}"
    assert pseudo_key in counts
    assert counts[pseudo_key] == {"ods": PSEUDO_TOTAL}

    for domain, expected in REGISTERED_BY_DOMAIN.items():
        got = counts[domain.name_cn]
        assert got == {lyr.value: n for lyr, n in expected.items()}

    assert len(counts) == 11 + 1  # 11 数据域 + 1 伪域
    assert sum(sum(per.values()) for per in counts.values()) == 88


# --------------------------------------------------------------------------- 与原文口径对账


def test_per_layer_reconciliation_with_source_explicit_list():
    """分层粒度对账：本项目 = 原文显式清单 + 补登记的表。"""
    added_by_layer: dict[Layer, int] = {}
    for name in PROJECT_ADDED_TABLES:
        spec = registry.by_name(name)
        added_by_layer[spec.layer] = added_by_layer.get(spec.layer, 0) + 1

    for layer in Layer:
        got = len(registry.by_layer(layer))
        expected = SOURCE_EXPLICIT_COUNTS[layer] + added_by_layer.get(layer, 0)
        assert got == expected, f"{layer.value.upper()} 层与原文显式清单对不上"


def test_project_added_tables_are_registered_and_documented():
    assert PROJECT_ADDED_TABLES == {
        "ods_production_kafka_event": "仅在原文分区策略全景表中出现，正文表名清单漏列",
    }
    for name in PROJECT_ADDED_TABLES:
        registry.by_name(name)  # 存在即可，不存在会 KeyError


def test_source_explicit_total_plus_added_equals_registry_total():
    assert sum(SOURCE_EXPLICIT_COUNTS.values()) == 87
    assert sum(SOURCE_EXPLICIT_COUNTS.values()) + len(PROJECT_ADDED_TABLES) == 88


def test_source_prose_figures_are_recorded_as_the_inconsistent_ones():
    """原文正文概述比显式清单少：ODS 少 4 张、DWD 少 3 张。登记下来，不悄悄抹平。"""
    assert SOURCE_EXPLICIT_COUNTS[Layer.ODS] - SOURCE_PROSE_ODS == 4
    assert SOURCE_EXPLICIT_COUNTS[Layer.DWD] - SOURCE_PROSE_DWD == 3


def test_domain_statistics_table_gate_count_is_one():
    """原文 11 数据域统计表口径「含质量门禁 1 张」——与本项目伪域表数一致。"""
    assert SOURCE_DOMAIN_TABLE_GATE_COUNT == PSEUDO_TOTAL == 1
