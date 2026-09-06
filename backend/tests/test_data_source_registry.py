"""Supported engines are listed honestly, and switching never leaks or corrupts state."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb

from app.service import data_sources
from app.service.data_sources import (
    ENGINES, DataSource, configured_sources, describe_destination, engine_from_url,
    normalize_engine, sql_dialect,
)

SUPPORTED = ("postgresql", "mysql", "doris", "starrocks", "clickhouse", "duckdb", "sqlite")


class EngineCatalogTests(unittest.TestCase):
    def test_every_requested_engine_is_supported_with_its_own_dialect(self):
        self.assertEqual(set(ENGINES), set(SUPPORTED))
        self.assertEqual(
            {engine: sql_dialect(engine) for engine in SUPPORTED},
            {"postgresql": "postgres", "mysql": "mysql", "doris": "doris",
             "starrocks": "starrocks", "clickhouse": "clickhouse",
             "duckdb": "duckdb", "sqlite": "sqlite"})

    def test_catalog_lists_unconfigured_engines_instead_of_hiding_them(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(data_sources, "CONFIG_PATH", Path("/nonexistent/llm_config.json")):
            sources = configured_sources()
        self.assertEqual({source.engine for source in sources}, set(SUPPORTED))
        unset = [source for source in sources if source.engine != "sqlite"]
        self.assertTrue(all(not source.available for source in unset))
        self.assertTrue(all("未配置连接串" in source.public()["unavailable_reason"] for source in unset))
        # The built-in demo warehouse needs no configuration to be selectable.
        demo = next(source for source in sources if source.engine == "sqlite")
        self.assertTrue(demo.available)
        self.assertEqual(demo.public()["unavailable_reason"], "")

    def test_doris_and_starrocks_are_told_apart_by_declared_type_not_url(self):
        # Both speak MySQL's wire protocol, so the scheme alone cannot identify them.
        self.assertEqual(engine_from_url("mysql+pymysql://h:1@d/db"), "mysql")
        self.assertEqual(normalize_engine("doris"), "doris")
        self.assertEqual(normalize_engine("StarRocks"), "starrocks")
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "llm_config.json"
            config.write_text(json.dumps({"database": {"connections": {
                "doris": {"url": "mysql+pymysql://user:secret@doris-host:9030/dw"},
                "starrocks": {"url": "mysql+pymysql://user:secret@sr-host:9030/dw"},
            }}}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(data_sources, "CONFIG_PATH", config):
                sources = {source.engine: source for source in configured_sources()}
        self.assertEqual(sources["doris"].dialect, "doris")
        self.assertEqual(sources["starrocks"].dialect, "starrocks")
        self.assertTrue(sources["doris"].available)

    def test_published_source_never_exposes_credentials(self):
        source = DataSource(id="config-mysql", engine="mysql",
                            url="mysql+pymysql://root:hunter2@warehouse-host:3306/dw_store")
        published = json.dumps(source.public(), ensure_ascii=False)
        self.assertNotIn("hunter2", published)
        self.assertNotIn("root", published)
        self.assertEqual(source.public()["destination"], "warehouse-host:3306/dw_store")
        self.assertEqual(describe_destination("postgresql://u:p@h:5432/db"), "h:5432/db")
        self.assertEqual(describe_destination("not a url"), "")

    def test_missing_driver_is_reported_rather_than_reported_as_available(self):
        source = DataSource(id="config-clickhouse", engine="clickhouse",
                            url="clickhouse+connect://user:pw@host:8123/db")
        with patch.object(data_sources, "driver_installed", return_value=False):
            published = source.public()
        self.assertFalse(published["available"])
        self.assertIn("驱动", published["unavailable_reason"])


class DataSourceSwitchingTests(unittest.TestCase):
    """Exercise a real two-source switch using only local, file-backed engines."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name) / "warehouse.duckdb"
        connection = duckdb.connect(str(path))
        connection.execute("CREATE TABLE dws_trade_order_daily "
                           "(dt DATE, category_name VARCHAR, gmv DOUBLE, order_count BIGINT)")
        connection.execute("INSERT INTO dws_trade_order_daily VALUES ('2026-09-04', '数码3C', 50000, 120)")
        connection.close()
        self.duckdb_source = DataSource(id="config-duckdb", engine="duckdb", url=f"duckdb:///{path}")

    def test_selected_source_answers_queries_in_its_own_dialect(self):
        from app.service.db_service import DBService
        database = DBService(source=self.duckdb_source)
        self.addCleanup(database.real_engine.dispose)
        self.assertEqual(database.active_db_type, "duckdb")
        self.assertIsNone(database.conn)
        result = database.execute_query(
            "SELECT SUM(gmv) AS total FROM dws_trade_order_daily", "doris")
        self.assertEqual(result.iloc[0]["total"], 50000)

    def test_demo_source_builds_the_fixture_without_touching_a_server(self):
        from app.service.db_service import DBService
        database = DBService(source=DataSource(id="demo-sqlite", engine="sqlite", origin="builtin"))
        self.addCleanup(database.conn.close)
        self.assertIsNone(database.real_engine)
        self.assertTrue(database.is_sample_data)
        self.assertEqual(database.execute_query("SELECT COUNT(*) AS n FROM articles").iloc[0]["n"], 12)

    def test_switching_is_reversible_and_keeps_each_source_isolated(self):
        from app.service.data_source_manager import DataSourceManager
        from app.service.db_service import DBService
        from app.service.semantic_layer import SemanticLayer

        demo = DataSource(id="demo-sqlite", engine="sqlite", origin="builtin")
        manager = DataSourceManager()
        database, layer = DBService(source=self.duckdb_source), SemanticLayer(database=None)
        self.addCleanup(database.real_engine.dispose)

        with patch("app.service.db_service.db_service", database), \
                patch("app.service.semantic_layer.semantic_layer", layer), \
                patch.object(DataSourceManager, "_refresh_derived_metadata"), \
                patch.object(data_sources, "find_source",
                             side_effect=lambda name: demo if name == "demo-sqlite" else None), \
                patch("app.service.data_source_manager.find_source",
                      side_effect=lambda name: demo if name == "demo-sqlite" else None):
            self.assertEqual(manager.adopt_current(), "active-duckdb")
            manager.activate("demo-sqlite")
            self.assertEqual(database.active_db_type, "sqlite")
            self.assertEqual(database.execute_query("SELECT COUNT(*) AS n FROM articles").iloc[0]["n"], 12)
            manager.activate("active-duckdb")
            # Returning to a source must restore its own connection, not the last one.
            self.assertEqual(database.active_db_type, "duckdb")
            self.assertEqual(database.execute_query(
                "SELECT SUM(gmv) AS total FROM dws_trade_order_daily", "doris").iloc[0]["total"], 50000)

    def test_unavailable_source_is_refused_without_changing_the_active_one(self):
        from app.service.data_source_manager import DataSourceError, DataSourceManager
        from app.service.db_service import DBService
        from app.service.semantic_layer import SemanticLayer

        manager = DataSourceManager()
        database, layer = DBService(source=self.duckdb_source), SemanticLayer(database=None)
        self.addCleanup(database.real_engine.dispose)
        with patch("app.service.db_service.db_service", database), \
                patch("app.service.semantic_layer.semantic_layer", layer):
            for name in ("unset-doris", "made-up-source"):
                with self.subTest(name=name), self.assertRaises(DataSourceError):
                    manager.activate(name)
            self.assertEqual(database.active_db_type, "duckdb")
            self.assertEqual(manager.active_id, "active-duckdb")


if __name__ == "__main__":
    unittest.main()
