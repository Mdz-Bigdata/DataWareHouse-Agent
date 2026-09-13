"""冒烟：Paimon + Neo4j 湖图双引擎血缘。

一句话契约：**湖仓管事实、图库管关系**。Paimon 是唯一事实源，Neo4j 只是关系视图，
属性永不双存。

红线①：实时链路失败不阻塞湖仓写入。所以 handle() 的所有失败路径都返回
ok=False 并投死信，绝不抛异常——这条在本文件里被直接打靶。

全程用假图库（FakeGraph）顶替 Neo4j，不装 neo4j 驱动、不起容器。
"""

from __future__ import annotations

from typing import Any

import pytest

from adas_lakehouse.lineage import (
    CONSTRAINT_STATEMENTS,
    MAX_TRAVERSAL_DEPTH,
    MIN_TRAVERSAL_DEPTH,
    NODE_SOURCE_TABLES,
    GraphPropertyPolicy,
    InMemoryFailureSink,
    LineageModelError,
    Neo4jGraphStore,
    Neo4jUnavailable,
    NodeLabel,
    RealtimeLineageSync,
    RelType,
    fact_from_row,
    sanitize_properties,
    validate_edges,
)

pytestmark = pytest.mark.smoke

DATA_ID = "COLLECT_BP_20260301123045_b7e2"
ARTIFACT_V4 = f"{DATA_ID}_slam_v4_b7e8f9a0"
ARTIFACT_V3 = f"{DATA_ID}_slam_v3_c4d5e6f7"
ARTIFACT_ALIGN = f"{DATA_ID}_align_v2_11223344"

ARTIFACT_TABLE = "dwd_production_artifact_detail"


def _artifact_row(**overrides) -> dict[str, Any]:
    row = {
        "artifact_id": ARTIFACT_V4,
        "data_id": DATA_ID,
        "step": "slam",
        "algo_version": "v4",
        "content_hash": "b7e8f9a0",
        "param_snapshot": '{"voxel": 0.1}',
        "parent_artifact_id": ARTIFACT_ALIGN,
        "status": "active",
    }
    row.update(overrides)
    return row


class FakeGraph:
    """进程内假图库。只记语句，不连 Neo4j。"""

    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.applied: list[Any] = []

    def apply(self, mutation, *, max_retries: int = 0) -> int:
        if self.broken:
            raise Neo4jUnavailable("假装图库挂了")
        statements = Neo4jGraphStore.merge_statements(mutation)
        self.applied.extend(statements)
        return len(statements)


# --------------------------------------------------------------------------- 湖仓行 → 图变更


def test_artifact_row_becomes_nodes_and_edges():
    fact = fact_from_row(ARTIFACT_TABLE, _artifact_row())
    assert fact.node_id == ARTIFACT_V4

    mutation = fact.to_mutation()
    labels = {(n.label, n.node_id) for n in mutation.nodes}
    assert (NodeLabel.ARTIFACT, ARTIFACT_V4) in labels
    assert (NodeLabel.CLIP, DATA_ID) in labels  # 产物必须挂回 clip
    assert (NodeLabel.ARTIFACT, ARTIFACT_ALIGN) in labels

    edges = {(e.rel, e.src_id, e.dst_id) for e in mutation.edges}
    assert (RelType.CONTAINS, DATA_ID, ARTIFACT_V4) in edges
    assert (RelType.DERIVED_FROM, ARTIFACT_V4, ARTIFACT_ALIGN) in edges
    assert validate_edges(mutation.edges) == []


def test_superseded_by_creates_the_version_branch():
    """重刷不是覆盖：旧产物保留，长出一条 SUPERSEDED_BY 指向新版本。"""
    row = _artifact_row(
        artifact_id=ARTIFACT_V3,
        algo_version="v3",
        content_hash="c4d5e6f7",
        superseded_by=ARTIFACT_V4,
        status="superseded",
    )
    mutation = fact_from_row(ARTIFACT_TABLE, row).to_mutation()
    edges = {(e.rel, e.src_id, e.dst_id) for e in mutation.edges}
    assert (RelType.SUPERSEDED_BY, ARTIFACT_V3, ARTIFACT_V4) in edges


def test_big_properties_never_reach_the_graph():
    """护栏：param_snapshot 这类大属性只留在湖仓，查询时按 ID 回取。"""
    mutation = fact_from_row(ARTIFACT_TABLE, _artifact_row()).to_mutation()
    artifact_node = next(n for n in mutation.nodes if n.node_id == ARTIFACT_V4)
    assert "param_snapshot" not in artifact_node.properties
    assert "content_hash" not in artifact_node.properties
    # 遍历键留下了，否则图库就没法按 step / 版本筛
    assert artifact_node.properties["step"] == "slam"
    assert artifact_node.properties["algo_version"] == "v4"


def test_sanitize_properties_guards_the_blacklist_both_ways():
    """写入端 strict=True 直接报错；对账链路 strict=False 静默丢弃。"""
    dirty = {"step": "slam", "param_snapshot": "{}", "quality_score": 0.99}

    with pytest.raises(LineageModelError, match="大属性"):
        sanitize_properties(NodeLabel.ARTIFACT, dirty)

    cleaned = sanitize_properties(NodeLabel.ARTIFACT, dirty, strict=False)
    assert cleaned == {"step": "slam"}
    assert "param_snapshot" not in cleaned
    assert "quality_score" not in cleaned

    # ID_ONLY 连遍历键都不留
    assert (
        sanitize_properties(
            NodeLabel.ARTIFACT, {"step": "slam"}, policy=GraphPropertyPolicy.ID_ONLY
        )
        == {}
    )


def test_id_column_mismatch_is_rejected_early():
    """「ID 本身即信息」：artifact_id 内嵌的 step/版本与列值必须一致。"""
    with pytest.raises(LineageModelError):
        fact_from_row(ARTIFACT_TABLE, _artifact_row(step="ann"))


# --------------------------------------------------------------------------- 实时同步链路二


def test_realtime_sync_writes_the_mutation_to_the_graph():
    graph = FakeGraph()
    sync = RealtimeLineageSync(graph=graph, failure_sink=InMemoryFailureSink())

    outcome = sync.handle(ARTIFACT_TABLE, _artifact_row())

    assert outcome.ok
    assert outcome.node_id == ARTIFACT_V4
    assert outcome.statements > 0
    assert graph.applied
    assert sync.stats["ok"] == 1
    assert sync.stats["failed"] == 0


def test_graph_outage_never_blocks_the_lakehouse():
    """红线①：图库挂了只记死信，绝不抛异常，等 T+1 对账补齐。"""
    sink = InMemoryFailureSink()
    sync = RealtimeLineageSync(graph=FakeGraph(broken=True), failure_sink=sink)

    outcome = sync.handle(ARTIFACT_TABLE, _artifact_row())

    assert not outcome.ok
    assert outcome.error
    assert len(sink) == 1
    assert sync.stats["failed"] == 1


def test_dirty_row_goes_to_dead_letter_without_raising():
    sink = InMemoryFailureSink()
    sync = RealtimeLineageSync(graph=FakeGraph(), failure_sink=sink)

    outcome = sync.handle(ARTIFACT_TABLE, _artifact_row(artifact_id="NOT_AN_ARTIFACT_ID"))

    assert not outcome.ok
    assert len(sink) == 1
    assert sync.stats["failed"] == 1


def test_injected_failure_sink_is_not_swallowed_by_a_falsy_check():
    """InMemoryFailureSink 定义了 __len__，空的是 falsy——用 `or` 判断就会被悄悄换掉。"""
    sink = InMemoryFailureSink()
    assert len(sink) == 0
    sync = RealtimeLineageSync(graph=FakeGraph(), failure_sink=sink)
    assert sync.failure_sink is sink


def test_unknown_table_is_handled_not_raised():
    sink = InMemoryFailureSink()
    sync = RealtimeLineageSync(graph=FakeGraph(), failure_sink=sink)
    outcome = sync.handle("dwd_not_a_lineage_table", _artifact_row())
    assert not outcome.ok
    assert len(sink) == 1


# --------------------------------------------------------------------------- 图语句与模型


def test_merge_statements_are_idempotent_merges_nodes_before_edges():
    mutation = fact_from_row(ARTIFACT_TABLE, _artifact_row()).to_mutation()
    statements = Neo4jGraphStore.merge_statements(mutation)

    cyphers = [str(s) for s in statements]
    assert all("MERGE" in c for c in cyphers)
    assert all("CREATE " not in c for c in cyphers)  # 只 MERGE，重放安全

    first_edge = next(i for i, c in enumerate(cyphers) if "-[" in c)
    assert all("-[" not in c for c in cyphers[:first_edge]), "节点语句必须排在边语句之前"


def test_node_source_tables_cover_all_five_labels():
    """五类节点每类都要说明自己从湖仓哪张表、哪个 ID 列来——图库不持有事实。

    表名与 ID 列都必须**逐字**落在 catalog.registry 上：血缘侧一旦自造别名，
    生成的 SQL 就会打在一张不存在的表上，而且一路到集群才会炸。
    """
    assert set(NODE_SOURCE_TABLES) == set(NodeLabel)
    from adas_lakehouse.catalog import registry

    known = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}
    for label, (table, id_column) in NODE_SOURCE_TABLES.items():
        assert table in known, f"{label.value} 的来源表 {table!r} 不在注册表里"
        assert id_column in known[table], f"{table}.{id_column} 不在注册表的字段里"


def test_reconcile_columns_all_exist_in_the_registry():
    """七类关系的对账列必须真的在湖仓表上——T+1 对账靠的就是这几列冗余。"""
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.lineage.model import REL_SPECS

    known = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}
    for rel, spec in REL_SPECS.items():
        if not spec.reconcile_table:
            continue
        assert spec.reconcile_table in known, f"{rel.value} 的对账表不在注册表里"
        assert spec.reconcile_column in known[spec.reconcile_table], (
            f"{rel.value} 的对账列 {spec.reconcile_table}.{spec.reconcile_column} 不在注册表里"
        )


def test_resolver_attribute_columns_all_exist_in_the_registry():
    """回湖仓补属性的列清单也要对得上，否则 SELECT 会整条失败。"""
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.lineage.model import NODE_SOURCE_TABLES as _sources
    from adas_lakehouse.lineage.resolver import ATTRIBUTE_COLUMNS

    known = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}
    for label, columns in ATTRIBUTE_COLUMNS.items():
        table, _ = _sources[label]
        missing = sorted(c for c in columns if c not in known[table])
        assert not missing, f"{label.value} 回取 {table} 的列 {missing} 不在注册表里"


def test_schema_constraints_cover_every_node_label():
    assert len(CONSTRAINT_STATEMENTS) == len(list(NodeLabel)) == 5
    joined = " ".join(CONSTRAINT_STATEMENTS)
    for label in NodeLabel:
        assert label.value in joined
    assert joined.count("IF NOT EXISTS") == 5  # 幂等，可反复执行


def test_traversal_depth_is_bounded():
    """受控遍历：深度闸门防止一条查询把整张图拉出来。"""
    from adas_lakehouse.lineage.graph import TraversalDepthError, resolve_depth

    assert MIN_TRAVERSAL_DEPTH <= MAX_TRAVERSAL_DEPTH
    assert resolve_depth(None) >= MIN_TRAVERSAL_DEPTH
    assert resolve_depth(MAX_TRAVERSAL_DEPTH) == MAX_TRAVERSAL_DEPTH
    with pytest.raises(TraversalDepthError):
        resolve_depth(MAX_TRAVERSAL_DEPTH + 1)
    assert resolve_depth(MAX_TRAVERSAL_DEPTH + 1, clamp=True) == MAX_TRAVERSAL_DEPTH


def test_neo4j_store_import_does_not_require_the_driver():
    """驱动缺失只在真正建连接时报错，不影响 import 与语句渲染。"""
    store = Neo4jGraphStore()
    assert store.config is not None
    with pytest.raises(Neo4jUnavailable):
        store.driver()
