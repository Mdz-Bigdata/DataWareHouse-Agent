"""四段式命名：{层级前缀}_{数据域}_{业务实体}_{粒度后缀}。

主线是原文给的三个实战拆解示例能被正确拆回四段：

    dwd_mining_image_tag_detail            DWD · 挖掘域 · image_tag · 明细
    dws_closed_loop_storage_cost_daily     DWS · 闭环域 · storage_cost · 日粒度
    ads_badcase_root_cause_distribution    ADS · 评测域（badcase 别名）· 无标准后缀

第三个示例是原文自己的偏离——域段写成 badcase_ 而不是规范的 evaluation_。
naming.py 用 DOMAIN_ALIASES 把它收编，见 docs/source-deviations.md。
"""

from __future__ import annotations

import pytest

from adas_lakehouse.domains import DataDomain, Layer
from adas_lakehouse.naming import (
    DOMAIN_ALIASES,
    GRANULARITY_SUFFIXES,
    build,
    lint,
    parse,
)

# --------------------------------------------------------------------------- 原文实战拆解示例


def test_dwd_mining_image_tag_detail():
    """原文示例①：DWD 层 + 挖掘域 + 图片标签 + 明细粒度。"""
    p = parse("dwd_mining_image_tag_detail")
    assert p.layer is Layer.DWD
    assert p.domain is DataDomain.MINING
    assert p.entity == "image_tag"
    assert p.suffix == "detail"
    assert p.is_canonical
    assert lint("dwd_mining_image_tag_detail") == []


def test_dws_closed_loop_storage_cost_daily():
    """原文示例②：闭环域前缀 closed_loop_ 必须靠最长前缀匹配切出来。"""
    p = parse("dws_closed_loop_storage_cost_daily")
    assert p.layer is Layer.DWS
    assert p.domain is DataDomain.CLOSED_LOOP
    assert p.entity == "storage_cost"
    assert p.suffix == "daily"
    assert p.is_canonical
    assert lint("dws_closed_loop_storage_cost_daily") == []


def test_ads_badcase_root_cause_distribution():
    """原文示例③：ADS 层的域段写成 badcase_，靠别名表归到评测域；无标准后缀。"""
    p = parse("ads_badcase_root_cause_distribution")
    assert p.layer is Layer.ADS
    assert p.domain is DataDomain.EVALUATION
    assert p.entity == "root_cause_distribution"
    assert p.suffix is None
    assert not p.is_canonical
    # ADS 层不强制第四段，所以不产生告警
    assert lint("ads_badcase_root_cause_distribution") == []
    assert DOMAIN_ALIASES["badcase"] is DataDomain.EVALUATION


# --------------------------------------------------------------------------- 拆解与拼装


@pytest.mark.parametrize(
    ("layer", "domain", "entity", "suffix"),
    [
        (Layer.DWD, DataDomain.MINING, "image_tag", "detail"),
        (Layer.DWS, DataDomain.CLOSED_LOOP, "storage_cost", "daily"),
        (Layer.DWS, DataDomain.PRODUCTION, "annotation_efficiency", "statistics"),
        (Layer.ODS, DataDomain.COLLECT, "vehicle", "info"),
        (Layer.ADS, DataDomain.EVALUATION, "model", "dashboard"),
    ],
)
def test_build_then_parse_round_trip(layer, domain, entity, suffix):
    name = build(layer, domain, entity, suffix)
    p = parse(name)
    assert (p.layer, p.domain, p.entity, p.suffix) == (layer, domain, entity, suffix)
    assert p.is_canonical


def test_longest_prefix_match_beats_shorter_candidates():
    """closed_loop_ / dataset_ 这类包含关系必须按最长前缀切，否则域会认错。"""
    assert parse("dwd_closed_loop_trace_chain").domain is DataDomain.CLOSED_LOOP
    assert parse("dwd_dataset_version_detail").domain is DataDomain.DATASET
    assert parse("dwd_collect_clip_detail").domain is DataDomain.COLLECT


def test_domain_by_prefix_accepts_both_spellings():
    assert DataDomain.by_prefix("mining_") is DataDomain.MINING
    assert DataDomain.by_prefix("mining") is DataDomain.MINING
    with pytest.raises(KeyError):
        DataDomain.by_prefix("nope")


def test_longest_prefix_match_returns_none_for_unknown_domain():
    assert DataDomain.longest_prefix_match("ads_hard_case_library") is None


# --------------------------------------------------------------------------- 非法输入


@pytest.mark.parametrize(
    "bad",
    [
        "DWD_mining_image_tag_detail",  # 层级前缀大写
        "dm_mining_image_tag_detail",  # 不是四层之一
        "dwd",  # 只有层级
        "dwd_",  # 实体为空
        "dwd_Mining_ImageTag",  # 含大写
        "dwd-mining-image-tag",  # 用短横线
        "1dwd_mining_x",
        "",
    ],
)
def test_parse_rejects_illegal_table_names(bad):
    with pytest.raises(ValueError, match="表名不合法"):
        parse(bad)


def test_lint_returns_message_instead_of_raising_for_illegal_name():
    """lint 永不抛异常——命名规范给的是可批量审计的告警，不是拦死的硬校验。"""
    notes = lint("NOT_A_TABLE")
    assert len(notes) == 1
    assert "表名不合法" in notes[0]


def test_build_rejects_non_standard_suffix():
    with pytest.raises(ValueError, match="非标准粒度后缀"):
        build(Layer.DWD, DataDomain.MINING, "image_tag", "detailz")


# --------------------------------------------------------------------------- lint 告警语义


def test_lint_flags_layer_mismatch():
    notes = lint("dwd_mining_image_tag_detail", expected_layer=Layer.DWS)
    assert any("与预期 dws 不符" in n for n in notes)


def test_lint_flags_missing_domain_segment():
    notes = lint("ods_vehicle_info")
    assert any("缺少可识别的数据域段" in n for n in notes)


def test_lint_flags_missing_suffix_only_for_dwd_and_dws():
    assert any("建议带标准粒度后缀" in n for n in lint("dwd_mining_image_tag"))
    assert any("建议带标准粒度后缀" in n for n in lint("dws_closed_loop_efficiency"))
    # ODS / ADS 不强制第四段
    assert not any("建议带标准粒度后缀" in n for n in lint("ods_collect_task"))
    assert not any("建议带标准粒度后缀" in n for n in lint("ads_hard_case_library"))


def test_lint_flags_suffix_used_in_unusual_layer():
    """_detail 惯用于 DWD；出现在 DWS 要告警（口径混层是最常见的规范滑坡）。"""
    notes = lint("dws_mining_image_tag_detail")
    assert any("惯用于" in n for n in notes)


def test_granularity_suffix_table_is_consistent():
    """每个标准后缀都必须声明它的惯用层级，且层级取自 Layer 枚举。"""
    assert GRANULARITY_SUFFIXES, "粒度后缀表不能为空"
    for suffix, (semantic, layers) in GRANULARITY_SUFFIXES.items():
        assert suffix.islower() and "_" not in suffix
        assert semantic
        assert layers and all(isinstance(x, Layer) for x in layers)


# --------------------------------------------------------------------------- 分层语义


def test_layer_system_fields_split_ods_from_the_rest():
    """系统字段规范：ODS 用 _source_system，其余层用 update_time。"""
    assert Layer.ODS.system_fields == ("_ingest_time", "_source_system")
    for layer in (Layer.DWD, Layer.DWS, Layer.ADS):
        assert layer.system_fields == ("_ingest_time", "update_time")


def test_every_layer_declares_a_purpose():
    for layer in Layer:
        assert layer.purpose
