# -*- coding: utf-8 -*-
"""Gap #15: lineage must be built from readable catalog metadata, never fabricated.

Every assertion here pins one of the four dishonesty defects the audit found in
`app/service/skills/lineage_skill.py`: a hardcoded 15-node/12-edge topology, a
fake Cypher "graph query", a hardcoded elapsed time, and a claim that the answer
came from a Paimon + Neo4j dual engine that this deployment does not run.
"""
import importlib
import os
import sqlite3
import unittest
from unittest.mock import patch

# Imports bootstrap an in-memory demo database only, never a configured physical one.
with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    lineage = importlib.import_module("app.service.skills.lineage_skill")
    db_module = importlib.import_module("app.service.db_service")
    semantic = importlib.import_module("app.service.semantic_layer")
from app.service.skills.base_skill import SkillContext
from app.schema.chat import AskResponse

# Node ids the previous implementation returned no matter what the data source held.
FABRICATED_NODES = [
    "ods_trade_order_raw", "ods_sensor_collection_raw", "dwd_trade_order_detail",
    "dwd_driving_frame_event", "dws_hardcase_mining_daily", "ads_trade_cockpit",
    "ads_closed_loop_efficiency", "ods_audio_play_log_raw", "dwd_audio_play_event",
    "dws_audio_album_daily", "ads_audio_album_rank",
]
# Engines/pipelines this repository does not deploy, so no answer may cite them.
UNDEPLOYED_CLAIMS = ["neo4j", "cypher", "paimon", "湖图", "双引擎", "flink cdc",
                     "starrocks", "spark", "derive_from", "三级 data_id"]


class FixtureDB:
    """A data source whose catalog is exactly what the test creates, nothing more."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.real_engine = None
        self.active_db_type = "sqlite"
        self.is_sample_data = False
        self.query_schemas = []


class LineageCatalogTests(unittest.TestCase):
    def setUp(self):
        with patch.object(semantic.SemanticLayer, "_initialize_registry"):
            self.layer = semantic.SemanticLayer()
        self.db = FixtureDB()
        self.addCleanup(self.db.conn.close)
        for target, name, value in (
            (lineage, "semantic_layer", self.layer),
            (lineage, "db_service", self.db),
            (db_module, "db_service", self.db),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.skill = lineage.LineageSkill()

    # ---------------- helpers ----------------
    def register(self, name, ddl, kind="TABLE"):
        self.db.conn.execute(f"CREATE {kind} {name} {ddl}" if kind == "TABLE"
                             else f"CREATE VIEW {name} AS {ddl}")
        rows = self.db.conn.execute(f"PRAGMA table_info({name})").fetchall()
        self.layer.discovered_table_columns[name] = [(row[1], row[2]) for row in rows]

    def fixture(self):
        """Two real tables joined by a real, declared foreign key."""
        self.register("dim_shop", "(id INTEGER PRIMARY KEY, shop_name TEXT)")
        self.register("dws_sales_daily",
                      "(dt TEXT, shop_id INTEGER, net_sales REAL, "
                      "FOREIGN KEY(shop_id) REFERENCES dim_shop(id))")

    def ask(self, question="这个指标的数据血缘是怎样的", **kwargs):
        return self.skill.execute(SkillContext(question=question, role="analyst", **kwargs))

    def edge(self, graph, source, target):
        return next((e for e in graph["edges"] if e["source"] == source and e["target"] == target), None)

    # ---------------- the fabricated topology ----------------
    def test_graph_contains_only_objects_the_data_source_actually_has(self):
        self.fixture()
        graph = self.skill.build_graph()
        self.assertEqual([node["id"] for node in graph["nodes"]], ["dim_shop", "dws_sales_daily"])
        for fake in FABRICATED_NODES:
            self.assertNotIn(fake, [node["id"] for node in graph["nodes"]])
        # The old graph was a constant 15 nodes / 12 edges regardless of the source.
        self.assertNotEqual(graph["stats"]["node_count"], 15)
        self.assertEqual(graph["stats"]["edge_count"], len(graph["edges"]))

    def test_every_edge_endpoint_is_a_real_node(self):
        self.fixture()
        self.layer.register_join_path(semantic.JoinPath(
            from_table="dws_sales_daily", to_table="ghost_dim", condition="x = y"))
        graph = self.skill.build_graph()
        ids = {node["id"] for node in graph["nodes"]}
        for edge in graph["edges"]:
            self.assertIn(edge["source"], ids)
            self.assertIn(edge["target"], ids)

    def test_declared_foreign_key_becomes_an_edge_labelled_as_declared(self):
        self.fixture()
        graph = self.skill.build_graph()
        edge = self.edge(graph, "dim_shop", "dws_sales_daily")
        self.assertIsNotNone(edge, graph["edges"])
        self.assertEqual(edge["evidence"], "foreign_key")
        self.assertIn("外键约束", edge["relation"])
        self.assertIn("dws_sales_daily.shop_id = dim_shop.id", edge["relation"])
        self.assertEqual(graph["stats"]["foreign_key_edges"], 1)

    def test_view_definition_is_parsed_into_a_real_derivation_edge(self):
        self.fixture()
        self.register("v_shop_sales", "SELECT dt, net_sales FROM dws_sales_daily", kind="VIEW")
        graph = self.skill.build_graph()
        edge = self.edge(graph, "dws_sales_daily", "v_shop_sales")
        self.assertIsNotNone(edge, graph["edges"])
        self.assertEqual(edge["evidence"], "view_definition")
        self.assertEqual(graph["stats"]["view_definition_edges"], 1)
        view_node = next(node for node in graph["nodes"] if node["id"] == "v_shop_sales")
        self.assertEqual(view_node["type"], "view")

    def test_inferred_join_path_is_marked_inferred_not_asserted_as_a_pipeline(self):
        self.register("dim_channel", "(id INTEGER PRIMARY KEY, channel_name TEXT)")
        self.register("fct_orders", "(channel_id INTEGER, amount REAL)")
        self.layer.register_join_path(semantic.JoinPath(
            from_table="fct_orders", to_table="dim_channel",
            condition="fct_orders.channel_id = dim_channel.id"))
        graph = self.skill.build_graph()
        edge = self.edge(graph, "dim_channel", "fct_orders")
        self.assertIsNotNone(edge, graph["edges"])
        self.assertEqual(edge["evidence"], "semantic_join_path")
        self.assertIn("推断", edge["relation"])
        self.assertIn("非数据库声明", edge["relation"])
        # No transformation engine may be attributed to a name-matching guess.
        for claim in UNDEPLOYED_CLAIMS:
            self.assertNotIn(claim, edge["relation"].lower())

    def test_declared_constraint_outranks_the_inferred_guess_for_the_same_pair(self):
        self.fixture()
        self.layer.register_join_path(semantic.JoinPath(
            from_table="dws_sales_daily", to_table="dim_shop",
            condition="dws_sales_daily.shop_id = dim_shop.id"))
        graph = self.skill.build_graph()
        pair = [e for e in graph["edges"] if (e["source"], e["target"]) == ("dim_shop", "dws_sales_daily")]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0]["evidence"], "foreign_key")
        self.assertIn("semantic_join_path", pair[0]["also_evidenced_by"])

    def test_layer_is_unknown_when_naming_gives_no_evidence(self):
        self.register("payments", "(id INTEGER PRIMARY KEY, amount REAL)")
        node = self.skill.build_graph()["nodes"][0]
        self.assertEqual(node["layer"], "UNKNOWN")
        self.assertEqual(node["domain"], "main")

    def test_repeated_builds_do_not_accumulate_duplicate_edges(self):
        self.fixture()
        first = self.skill.build_graph()
        self.ask()
        self.ask()
        second = self.skill.build_graph()
        self.assertEqual(len(first["edges"]), len(second["edges"]))
        self.assertEqual(first["edges"], second["edges"])

    # ---------------- insufficient data instead of invention ----------------
    def test_empty_catalog_reports_insufficient_data_and_no_graph(self):
        result = self.ask()
        self.assertFalse(result.success)
        self.assertIn("数据不足", result.error)
        self.assertIsNone(result.lineage_data)
        self.assertEqual(result.data, [])
        self.assertIsNone(result.chart)
        AskResponse.model_validate(result.model_dump())

    def test_tables_without_relationships_state_no_lineage_was_found(self):
        self.register("standalone_events", "(id INTEGER PRIMARY KEY, payload TEXT)")
        result = self.ask()
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.lineage_data["edges"], [])
        self.assertIn("数据不足", result.conclusion)
        self.assertIn("不构成加工链路", result.conclusion)

    def test_unreadable_catalog_degrades_without_inventing_relationships(self):
        self.register("orders_raw", "(id INTEGER PRIMARY KEY)")
        self.db.conn.close()  # catalog reads now raise
        graph = self.skill.build_graph()
        self.assertEqual([node["id"] for node in graph["nodes"]], ["orders_raw"])
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["nodes"][0]["type"], "unknown")

    # ---------------- the fake Cypher, timing and engine claims ----------------
    def test_no_fake_graph_query_is_reported_as_executed_sql(self):
        self.fixture()
        result = self.ask()
        self.assertEqual(result.details["sql"], "")
        self.assertEqual(result.details["filters"], [])

    def test_elapsed_time_is_measured_not_hardcoded(self):
        self.fixture()
        with patch.object(lineage.time, "perf_counter", side_effect=[10.0, 10.5]):
            result = self.ask()
        self.assertEqual(result.details["elapsed_time"], "0.500s")
        self.assertNotEqual(result.details["elapsed_time"], "0.006s")

    def test_answer_never_cites_an_engine_this_deployment_does_not_run(self):
        self.fixture()
        result = self.ask()
        self.assertTrue(result.success, result.error)
        text = " ".join([result.conclusion, result.details["sql"], result.details["source_desc"],
                         self.skill.description]).lower()
        for claim in UNDEPLOYED_CLAIMS:
            self.assertNotIn(claim, text)
        self.assertIn("未接入图数据库", result.conclusion)
        self.assertIsNone(result.lineage_data["stats"]["graph_engine"])

    def test_details_describe_the_real_source_and_real_tables(self):
        self.fixture()
        result = self.ask()
        self.assertEqual(result.details["tables"], ["dim_shop", "dws_sales_daily"])
        self.assertEqual(result.details["data_source"], "demo")
        self.assertIn("目录元数据", result.details["source_desc"])
        self.assertEqual(result.details["estimated_rows"], 2)
        AskResponse.model_validate(result.model_dump())

    # ---------------- focus resolution ----------------
    def test_focus_comes_from_a_registered_metric_not_a_hardcoded_table(self):
        self.fixture()
        self.layer.register_metric(semantic.Metric(
            name="net_sales_total", aliases=["净销售额"], description="real metric",
            calculation="net_sales", unit="元", available_dimensions=[],
            default_agg="SUM", source_table="dws_sales_daily"))
        result = self.ask("净销售额的数据血缘")
        self.assertIn("焦点对象「dws_sales_daily」", result.conclusion)
        self.assertIn("直接上游 dim_shop", result.conclusion)
        self.assertIn("SUM(net_sales)", result.conclusion)
        self.assertNotIn("dws_trade_order_daily", result.conclusion)

    def test_unrecognised_subject_yields_no_focus_instead_of_a_default_one(self):
        self.fixture()
        result = self.ask("血缘链路是怎样的")
        self.assertIn("未能从提问中确定具体的表或指标", result.conclusion)
        self.assertNotIn("焦点对象", result.conclusion)

    def test_explicit_physical_table_is_honoured_as_focus(self):
        self.fixture()
        result = self.ask("dws_sales_daily 的上游依赖")
        self.assertIn("焦点对象「dws_sales_daily」", result.conclusion)


class LineageEngineCatalogTests(unittest.TestCase):
    """The configured-engine path (production runs PostgreSQL) reads a real catalog."""

    def setUp(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import StaticPool

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE dim_store (id INTEGER PRIMARY KEY, store_name TEXT)"))
            connection.execute(text("CREATE TABLE ads_revenue (store_id INTEGER, revenue REAL, "
                                    "FOREIGN KEY(store_id) REFERENCES dim_store(id))"))
            connection.execute(text("CREATE VIEW v_revenue AS SELECT revenue FROM ads_revenue"))

        with patch.object(semantic.SemanticLayer, "_initialize_registry"):
            self.layer = semantic.SemanticLayer()
        for table in ("dim_store", "ads_revenue", "v_revenue"):
            self.layer.discovered_table_columns[table] = [("id", "INTEGER")]

        self.db = FixtureDB()
        self.addCleanup(self.db.conn.close)
        self.db.real_engine = engine
        for target, name, value in ((lineage, "semantic_layer", self.layer),
                                    (lineage, "db_service", self.db)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.skill = lineage.LineageSkill()

    def test_engine_catalog_yields_constraint_and_view_edges(self):
        graph = self.skill.build_graph()
        pairs = {(edge["source"], edge["target"]): edge["evidence"] for edge in graph["edges"]}
        self.assertEqual(pairs.get(("dim_store", "ads_revenue")), "foreign_key")
        self.assertEqual(pairs.get(("ads_revenue", "v_revenue")), "view_definition")
        types = {node["id"]: node["type"] for node in graph["nodes"]}
        self.assertEqual(types, {"dim_store": "table", "ads_revenue": "table", "v_revenue": "view"})
        layers = {node["id"]: node["layer"] for node in graph["nodes"]}
        self.assertEqual(layers["ads_revenue"], "ADS")
        self.assertEqual(layers["v_revenue"], "UNKNOWN")

    def test_configured_source_is_reported_as_configured(self):
        result = self.skill.execute(SkillContext(question="ads_revenue 的血缘", role="analyst"))
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.details["data_source"], "configured")
        self.assertNotIn("项目示例数据", result.details["source_desc"])
        AskResponse.model_validate(result.model_dump())


class LineageSchemaPrecedenceTests(unittest.TestCase):
    """A name defined in several schemas must resolve like an unqualified query does."""

    def setUp(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import StaticPool

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("ATTACH DATABASE ':memory:' AS warehouse"))
            connection.execute(text("CREATE TABLE dim_store (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE ads_revenue (store_id INTEGER, "
                                    "FOREIGN KEY(store_id) REFERENCES dim_store(id))"))
            # Same name in the lower-precedence schema, wired to a different parent.
            connection.execute(text("CREATE TABLE warehouse.dim_store (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE warehouse.ads_revenue (other INTEGER)"))
            connection.execute(text("CREATE TABLE warehouse.extra_dim (id INTEGER)"))

        with patch.object(semantic.SemanticLayer, "_initialize_registry"):
            self.layer = semantic.SemanticLayer()
        for table in ("dim_store", "ads_revenue", "extra_dim"):
            self.layer.discovered_table_columns[table] = [("id", "INTEGER")]

        self.db = FixtureDB()
        self.addCleanup(self.db.conn.close)
        self.db.real_engine = engine
        self.db.query_schemas = ["main", "warehouse"]
        for target, name, value in ((lineage, "semantic_layer", self.layer),
                                    (lineage, "db_service", self.db)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.skill = lineage.LineageSkill()

    def test_objects_resolve_to_the_first_schema_that_defines_them(self):
        graph = self.skill.build_graph()
        schemas = {node["id"]: node["schema"] for node in graph["nodes"]}
        self.assertEqual(schemas, {"dim_store": "main", "ads_revenue": "main",
                                   "extra_dim": "warehouse"})
        self.assertEqual([(e["source"], e["target"], e["evidence"]) for e in graph["edges"]],
                         [("dim_store", "ads_revenue", "foreign_key")])

    def test_constraint_pointing_at_another_schema_is_not_reattached_locally(self):
        info = {"child": {"schema": "public", "type": "table"},
                "dim_x": {"schema": "public", "type": "table"}}
        edges = []
        foreign_key = {"referred_table": "dim_x", "constrained_columns": ["x_id"],
                       "referred_columns": ["id"]}
        lineage.LineageSkill._collect_foreign_keys(
            "child", [dict(foreign_key, referred_schema="staging")], info, edges)
        self.assertEqual(edges, [])
        lineage.LineageSkill._collect_foreign_keys(
            "child", [dict(foreign_key, referred_schema="public")], info, edges)
        self.assertEqual(len(edges), 1)


class LineageContractTests(unittest.TestCase):
    """The routing and the /chat/lineage payload shape must stay compatible."""

    def setUp(self):
        self.skill = lineage.LineageSkill()

    def test_routing_confidence_is_unchanged(self):
        for question in ("GMV指标的数据血缘是怎样的", "这个表的上游是什么", "数据从哪来"):
            self.assertEqual(self.skill.can_handle(SkillContext(question=question)), (True, 0.95))
        for question in ("按来源统计文章", "各来源的文章数量", "source_platform 分组"):
            self.assertEqual(self.skill.can_handle(SkillContext(question=question)), (False, 0.0))

    def test_lineage_graph_attribute_still_serves_the_api_shape(self):
        graph = lineage.lineage_skill.lineage_graph
        self.assertEqual(set(graph) >= {"nodes", "edges"}, True)
        for node in graph["nodes"]:
            self.assertLessEqual({"id", "name", "layer", "type", "domain"}, set(node))
        for edge in graph["edges"]:
            self.assertLessEqual({"source", "target", "relation"}, set(edge))


if __name__ == "__main__":
    unittest.main()
