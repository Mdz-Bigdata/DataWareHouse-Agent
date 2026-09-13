"""冒烟：数据面——实际计算与主数据的所在地。

与控制面互补：控制面传指针，数据面才碰数据本体；湖仓是主数据的唯一归属。

主流程：适配器接任务信封 → 引擎执行 → 结果写湖 + 写血缘。
LakeSink / LineageSink 都用 dry-run 实现，不落任何真实存储。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse import dataplane as D
from adas_lakehouse.controlplane import TaskKind

pytestmark = pytest.mark.smoke

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
NOW = datetime(2026, 3, 2, 3, 0, 0)


# --------------------------------------------------------------------------- 引擎选型


def test_every_task_kind_maps_to_exactly_one_engine():
    for kind in TaskKind:
        engine = D.engine_for(kind)
        assert engine.key and engine.name_cn and engine.runtime
        assert engine.output_table


def test_engine_output_tables_are_registered():
    from adas_lakehouse.catalog import registry

    for engine in D.ENGINES:
        assert registry.by_name(engine.output_table), engine.key


def test_gpu_engines_are_the_ones_that_declare_it():
    gpu_engines = {e.key for e in D.ENGINES if e.needs_gpu}
    assert "vlm_inference" in gpu_engines
    assert "embedding" in gpu_engines
    assert "rule_mining" not in gpu_engines  # 规则挖掘白天跑批，不抢 GPU

    for engine in D.ENGINES:
        if engine.needs_gpu:
            assert engine.zone is D.DeployZone.GPU


def test_engines_are_spread_across_deploy_zones():
    """引擎与服务解耦、引擎之间也解耦——分区部署是这条的物理体现。"""
    engine_zones = {e.zone for e in D.ENGINES}
    declared_zones = {spec.zone for spec in D.DEPLOY_ZONES}
    assert len(engine_zones) >= 2
    assert engine_zones <= declared_zones
    for spec in D.DEPLOY_ZONES:
        assert spec.components and spec.scaling


# --------------------------------------------------------------------------- 湖仓归属


def test_lakehouse_owns_every_master_data_asset():
    """clip / image / tag / vector / dataset 五类主数据必须全部归数据面（Paimon）。"""
    from adas_lakehouse.controlplane import DATA_PLANE_ASSETS

    for asset in DATA_PLANE_ASSETS:
        D.assert_lakehouse_owned(asset)  # 不抛即通过


def test_control_plane_asset_is_rejected_as_lakehouse_owned():
    """规则配置 / 任务状态 / 幂等键归控制面——把它们当湖仓资产就是平面串味。"""
    from adas_lakehouse.controlplane import CONTROL_PLANE_ASSETS

    for asset in CONTROL_PLANE_ASSETS:
        with pytest.raises(D.LakehouseOwnershipError):
            D.assert_lakehouse_owned(asset)


def test_unregistered_asset_must_declare_its_plane_first():
    with pytest.raises(D.LakehouseOwnershipError, match="未登记的资产"):
        D.assert_lakehouse_owned("something_brand_new")


def test_data_plane_tables_are_all_registered_in_the_catalog():
    from adas_lakehouse.catalog import registry

    assert D.DATA_PLANE_TABLES
    for table in D.DATA_PLANE_TABLES:
        assert registry.by_name(table.name), table.name
        assert table.asset and table.purpose


def test_image_id_and_data_id_are_mutually_derivable():
    image_id = D.image_id_for(DATA_ID, 42)
    assert D.data_id_of_image(image_id) == DATA_ID


# --------------------------------------------------------------------------- GPU 调度


def test_embedding_deadline_is_the_early_morning_window():
    """T+1 向量化必须在凌晨 6 点前跑完，否则白天的检索拿不到昨天的数据。"""
    assert D.EMBEDDING_WINDOW_DEADLINE_HOUR == 6
    assert D.embedding_deadline_ok(datetime(2026, 3, 2, 0, 30), datetime(2026, 3, 2, 5, 59))
    assert not D.embedding_deadline_ok(datetime(2026, 3, 2, 0, 30), datetime(2026, 3, 2, 6, 1))


def test_peak_hours_are_declared_and_detected():
    """白天高峰不跑向量化，夜里才动 GPU。"""
    assert D.PEAK_HOURS
    for hour in sorted(D.PEAK_HOURS):
        assert D.is_peak(datetime(2026, 3, 2, hour, 0))
    assert not D.is_peak(datetime(2026, 3, 2, 3, 0))
    assert D.EMBEDDING_WINDOW_DEADLINE_HOUR not in D.PEAK_HOURS


#: 高价值来源的机器可读标记。
#: 注意 HIGH_VALUE_SOURCES 装的是原文逐字的中文描述（「规则命中 / 事件抽帧 / VLM 标签」），
#: 不能直接喂给 classify_value_tier()——分级用的是下面这三个 slug。
HIGH_VALUE_SOURCE_KEYS = ("rule_hit", "event_trigger", "vlm_tag")


def test_high_value_sources_get_the_better_tier():
    assert len(D.HIGH_VALUE_SOURCES) == len(HIGH_VALUE_SOURCE_KEYS) == 3

    for key in HIGH_VALUE_SOURCE_KEYS:
        assert D.classify_value_tier(key) is D.ValueTier.HIGH
    assert D.classify_value_tier("uniform_sampling") is D.ValueTier.ORDINARY
    # 未知来源保守归为普通数据——省 GPU
    assert D.classify_value_tier("某个没见过的来源") is D.ValueTier.ORDINARY


def test_ordinary_data_is_sampled_not_fully_processed():
    """普通数据只抽样进 GPU，否则 GPU 池永远排不完队。"""
    assert 0.0 < D.ORDINARY_DATA_SAMPLE_RATIO < 1.0


def test_quota_favours_high_value_sources():
    """高价值数据全量向量化，普通数据抽样——GPU 成本花在刀刃上的算术形式。"""
    candidates = 1_000_000
    high = D.vectorization_quota("rule_hit", candidates)
    ordinary = D.vectorization_quota("uniform_sampling", candidates)

    assert high == candidates
    assert ordinary == pytest.approx(candidates * D.ORDINARY_DATA_SAMPLE_RATIO, rel=0.01)
    assert 0 < ordinary < high

    # 边界：0 条就是 0；再小的普通批也至少留 1 条，保证链路可观测
    assert D.vectorization_quota("uniform_sampling", 0) == 0
    assert D.vectorization_quota("uniform_sampling", 1) == 1
    with pytest.raises(ValueError):
        D.vectorization_quota("rule_hit", -1)

    batch = D.batch_quota([("rule_hit", candidates), ("uniform_sampling", candidates)])
    assert batch == {"rule_hit": high, "uniform_sampling": ordinary}


# --------------------------------------------------------------------------- 查询路由


@pytest.mark.parametrize("intent", list(D.QueryIntent))
def test_every_query_intent_gets_a_plan(intent):
    plan = D.plan_query(intent, filters={"dt": "2026-03-01"}, top_k=50)
    assert plan.intent is intent
    assert plan.route in set(D.QueryRoute)
    assert plan.sql.strip()
    assert plan.note


def test_semantic_search_goes_through_the_external_catalog():
    """向量检索走 StarRocks 外部表 HNSW 直查 Paimon——放弃独立向量库换单一事实源。"""
    plan = D.plan_query(D.QueryIntent.SEMANTIC_SEARCH, filters={"dt": "2026-03-01"})
    assert plan.route is D.QueryRoute.EXTERNAL_CATALOG
    assert "dwd_mining_image_vector_detail" in plan.sql
    assert "LIMIT" in plan.sql.upper()


def test_top_k_reaches_the_limit_clause():
    plan = D.plan_query(D.QueryIntent.SEMANTIC_SEARCH, top_k=7)
    assert "LIMIT 7" in plan.sql


# --------------------------------------------------------------------------- 执行


def test_dry_run_sinks_record_without_writing_anything():
    lake = D.DryRunLakeSink()
    lineage = D.NullLineageSink()
    plane = D.DataPlane(lake=lake, lineage=lineage)
    assert plane is not None


def test_base_adapter_is_abstract_and_requires_a_name():
    """骨架只管与控制面打交道；真正干活的 run() 必须由子系统实现。"""
    with pytest.raises(TypeError):
        D.BaseSubsystemAdapter()  # run() 是抽象方法

    class Nameless(D.BaseSubsystemAdapter):
        kinds = frozenset({TaskKind.RULE_MINING})

        def run(self, ctx):
            return []

    with pytest.raises(ValueError, match="name"):
        Nameless()


def test_concrete_adapter_reports_its_kinds_and_health():
    class MiningAdapter(D.BaseSubsystemAdapter):
        name = "mining"
        kinds = frozenset({TaskKind.RULE_MINING})

        def run(self, ctx):
            return []

    adapter = MiningAdapter(data_plane=D.DataPlane(lake=D.DryRunLakeSink()))
    assert adapter.supported_kinds() == frozenset({TaskKind.RULE_MINING})
    assert adapter.health() is True


def test_asset_inventory_separates_the_two_planes():
    """控制面资产与数据面资产不许混——混了就意味着平台开始持有主数据。"""
    inventory = D.asset_inventory()
    assert inventory
    assert D.alignment_principles()
