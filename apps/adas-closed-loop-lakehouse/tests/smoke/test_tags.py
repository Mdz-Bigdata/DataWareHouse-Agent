"""冒烟：统一标签体系——三来源字典映射与去重治理（防标签爆炸）。

主流程：三来源 RawTag 进 → 字典映射归一 → 幂等去重 / 冲突消解 → DataTagRecord 出，
未命中字典的进候选池等评审。写出口用 InMemoryTagWriter，不连 Flink。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.tags import (
    CAPTION_TAG_CATEGORY,
    COVERAGE_TABLE,
    DATA_TAG_TABLE,
    DICT_TABLE,
    IMAGE_TAG_TABLE,
    MODEL_TAG_MIN_CONFIDENCE,
    TAG_CATEGORY_COUNT,
    TAG_SOURCE_COUNT,
    InMemoryTagWriter,
    RawTag,
    TagCategory,
    TagSource,
    UnifiedTagService,
    compute_daily_coverage,
    default_dictionary,
    normalize_tag_text,
    slugify_tag_id,
    source_priority,
)
from adas_lakehouse.tags import tables as tag_tables
from adas_lakehouse.tags.constants import VECTOR_TABLE

pytestmark = pytest.mark.smoke

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
TAG_TIME = datetime(2026, 3, 1, 12, 30, 45)


def _raw(text: str, source: TagSource, **overrides) -> RawTag:
    payload = {
        "raw_tag": text,
        "source": source,
        "data_id": DATA_ID,
        "tag_time": TAG_TIME,
    }
    if source is TagSource.RULE:
        payload.update(rule_id="R-RAIN-001", rule_version="v1")
    if source is TagSource.MODEL:
        payload.update(confidence=0.95, model_name="vlm", model_version="v2")
    payload.update(overrides)
    return RawTag(**payload)


def _service() -> UnifiedTagService:
    return UnifiedTagService(writer=InMemoryTagWriter())


# --------------------------------------------------------------------------- 三来源收口


def test_three_sources_collapse_into_one_dictionary_tag():
    """采集 / 规则 / 模型三条来源说的是同一件事，落表只该有一条标准标签。"""
    svc = _service()
    result = svc.ingest(
        [
            _raw("雨天", TagSource.COLLECT),
            _raw("雨天", TagSource.RULE),
            _raw("rain", TagSource.MODEL),
        ],
        started_at=TAG_TIME,
    )

    assert result.accepted_count >= 1
    tag_ids = {r.tag_id for r in result.data_tags}
    assert len(tag_ids) == 1, f"三来源没有收口到同一个 tag_id: {tag_ids}"
    assert result.dedup_stats.received >= len(result.data_tags)


def test_unmapped_tag_goes_to_the_candidate_pool_not_into_the_dictionary():
    """防标签爆炸的核心：模型自由发挥的新词先进候选池，评审通过才进字典。"""
    svc = _service()
    result = svc.ingest([_raw("一种前所未见的天气", TagSource.MODEL)], started_at=TAG_TIME)

    assert result.data_tags == () or len(result.data_tags) == 0
    assert len(result.candidates) == 1
    assert len(result.rejected) == 1


def test_source_priority_is_total_and_deterministic():
    """冲突消解要有确定的优先级，否则同一批数据跑两次结果不同。"""
    priorities = {src: source_priority(src) for src in TagSource}
    assert len(set(priorities.values())) == TAG_SOURCE_COUNT == 3


def test_low_confidence_model_tag_is_not_accepted():
    svc = _service()
    below = MODEL_TAG_MIN_CONFIDENCE / 2
    result = svc.ingest([_raw("雨天", TagSource.MODEL, confidence=below)], started_at=TAG_TIME)
    assert len(result.data_tags) == 0
    assert len(result.rejected) == 1


def test_ingest_is_idempotent_on_replay():
    """同一批重放：联合主键 Upsert，行数不翻倍。"""
    raws = [_raw("雨天", TagSource.COLLECT), _raw("高速", TagSource.COLLECT)]

    first = _service().ingest(raws, started_at=TAG_TIME)
    second = _service().ingest(raws, started_at=TAG_TIME)

    key = lambda res: sorted((r.data_id, r.tag_id) for r in res.data_tags)  # noqa: E731
    assert key(first) == key(second)
    assert len(set(key(first))) == len(first.data_tags)


def test_writer_receives_the_rows():
    writer = InMemoryTagWriter()
    svc = UnifiedTagService(writer=writer)
    result = svc.ingest([_raw("雨天", TagSource.COLLECT)], started_at=TAG_TIME)
    written = svc.flush(result)
    assert sum(written.values()) >= 1
    assert writer.rows or writer.buffers


def test_run_id_is_stamped_on_every_pipeline_result():
    result = _service().ingest([_raw("雨天", TagSource.COLLECT)], started_at=TAG_TIME)
    assert result.run_id.startswith("run_")
    from adas_lakehouse.ids import parse_run_id

    parse_run_id(result.run_id)  # 必须是合法的三级 run_id


# --------------------------------------------------------------------------- 字典


def test_default_dictionary_covers_the_five_business_categories():
    """五大业务类别（场景 / 环境 / 道路 / 参与者 / 行为）。

    CAPTION 是第六个，但它不是受控词表——它装的是 VLM 生成的自由文本描述，
    所以 TAG_CATEGORY_COUNT 只数 5 个，这里显式把它排除掉。
    """
    counts = default_dictionary().counts_by_category()
    business = {k: v for k, v in counts.items() if k != CAPTION_TAG_CATEGORY}
    assert len(business) == TAG_CATEGORY_COUNT == 5
    assert all(v > 0 for v in business.values())
    assert CAPTION_TAG_CATEGORY in counts


def test_dictionary_is_internally_valid():
    """层级树、别名、四态、同层互斥——字典自检必须干净，否则映射结果不可预期。"""
    dictionary = default_dictionary()
    assert dictionary.validate() == []

    entries = [e for cat in TagCategory for e in dictionary.by_category(cat)]
    ids = [e.tag_id for e in entries]
    assert len(set(ids)) == len(ids), "字典里有重复的 tag_id"
    for entry in entries:
        assert dictionary.get(entry.tag_id) is not None


def test_tag_text_normalisation_is_stable():
    """归一化必须幂等，否则同一个词会因为空格 / 全半角分裂成两个标签。"""
    assert normalize_tag_text(" 雨天 ") == normalize_tag_text("雨天")
    once = normalize_tag_text("Heavy  Rain")
    assert normalize_tag_text(once) == once


def test_slugify_produces_a_stable_tag_id():
    assert slugify_tag_id("Heavy Rain") == slugify_tag_id("heavy  rain")
    assert " " not in slugify_tag_id("Heavy Rain")


def test_dictionary_health_reports_something_actionable():
    svc = _service()
    health = svc.dictionary_health()
    assert isinstance(health, dict)
    assert health


# --------------------------------------------------------------------------- 表结构对齐


def test_tags_does_not_redefine_its_tables():
    """表结构的唯一事实源是 catalog.registry，本子系统只引用，不自带一份。"""
    from adas_lakehouse.catalog import registry

    assert tag_tables.TABLES, "四张表一张都没取到"
    for spec in tag_tables.TABLES:
        # 同一个对象，不是拷贝——拷贝会各自漂移，那正是两套结构并存的起点
        assert registry.by_name(spec.name) is spec


def test_every_written_column_exists_in_the_registry():
    """列不存在 = INSERT 直接失败 / SELECT 读到 NULL，所以每个落库行的列名都钉死。"""
    svc = _service()
    result = svc.ingest([_raw("雨天", TagSource.COLLECT)], started_at=TAG_TIME)

    for record in result.data_tags:
        tag_tables.check_row(DATA_TAG_TABLE, record.to_row())

    inherited = svc.inherit_to_images(result.data_tags, {DATA_ID: ["IMG_0001"]})
    assert inherited, "可继承标签没继承到图片，继承能力被改没了"
    for record in inherited:
        tag_tables.check_row(IMAGE_TAG_TABLE, record.to_row())

    caption = svc.ingest_caption(
        "IMG_0001", DATA_ID, "前方大雨，能见度低", model_name="vlm", model_version="v2"
    )
    tag_tables.check_row(IMAGE_TAG_TABLE, caption.to_row())
    # caption 冗余到向量表的那一行同样走 registry 的列名
    tag_tables.check_row(VECTOR_TABLE, svc.caption_vector_row(caption))

    for entry in default_dictionary():
        tag_tables.check_row(DICT_TABLE, entry.to_row())

    rows = compute_daily_coverage(
        "2026-03-01",
        data_tags=result.data_tags,
        total_data_count=10,
        total_image_count=40,
        project_code="PROJ_A",
    )
    for row in rows:
        tag_tables.check_row(COVERAGE_TABLE, row.to_row())


def test_every_written_row_carries_the_whole_primary_key():
    """主键缺一段，Upsert 幂等就不成立——原文②的「重跑不产生重复标签」全靠它。"""
    svc = _service()
    result = svc.ingest([_raw("雨天", TagSource.COLLECT)], started_at=TAG_TIME)
    coverage = compute_daily_coverage("2026-03-01", project_code="PROJ_A")

    cases = [
        (DATA_TAG_TABLE, result.data_tags[0].to_row()),
        (DICT_TABLE, next(iter(default_dictionary())).to_row()),
        (COVERAGE_TABLE, coverage[0].to_row()),
    ]
    for table, row in cases:
        for key in tag_tables.spec(table).primary_key:
            assert key in row, f"{table} 的行缺主键字段 {key}"


def test_conflict_loser_is_written_as_invalid_not_deleted():
    """互斥裁决落败方落库是 tag_status=invalid（内存里的 valid_flag=False），行不删。"""
    svc = _service()
    result = svc.ingest([_raw("雨天", TagSource.COLLECT)], started_at=TAG_TIME)
    record = result.data_tags[0]

    assert record.to_row()["tag_status"] == "active"
    record.valid_flag = False
    assert record.to_row()["tag_status"] == "invalid"


def test_check_row_rejects_a_column_the_registry_does_not_have():
    """自检本身要有效：本地私自加一列必须当场被拦下。"""
    with pytest.raises(ValueError, match="不在 registry"):
        tag_tables.check_row(DATA_TAG_TABLE, {"data_id": "x", "definitely_not_a_column": 1})
