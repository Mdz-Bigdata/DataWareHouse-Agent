"""Regression checks for physical queries being replaced by demo data."""
import os
import unittest
from unittest.mock import patch

from pandas.errors import DatabaseError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    from app.service.db_service import DBService
    from app.service.semantic_layer import SemanticLayer


class DataSourceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DB_TYPE": "sqlite"})
        self.env.start()
        self.db = DBService()
        self.engine = create_engine("sqlite:///:memory:")
        self.db.real_engine = self.engine
        self.db.active_db_type = "sqlite"

    def tearDown(self):
        self.db.conn.close()
        self.engine.dispose()
        self.env.stop()

    def test_physical_warehouse_table_takes_precedence_over_demo(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE dws_trade_order_daily (refund_amount REAL)"))
            conn.execute(text("INSERT INTO dws_trade_order_daily VALUES (7.5)"))
        result = self.db.execute_query(
            "SELECT SUM(refund_amount) AS amount FROM dws_trade_order_daily"
        )
        self.assertEqual(result.iloc[0]["amount"], 7.5)

    def test_missing_physical_table_never_returns_demo_rows(self):
        with self.assertRaises((DatabaseError, SQLAlchemyError)):
            self.db.execute_query("SELECT refund_amount FROM dws_trade_order_daily")

    def test_table_name_in_literal_does_not_change_data_source(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE live_orders (amount REAL)"))
            conn.execute(text("INSERT INTO live_orders VALUES (42)"))
        result = self.db.execute_query(
            "SELECT amount, 'dws_trade_order_daily' AS label FROM live_orders"
        )
        self.assertEqual(result.iloc[0]["amount"], 42)

    def test_sqlite_mode_keeps_demo_queries_available(self):
        self.db.real_engine = None
        result = self.db.execute_query("SELECT refund_amount FROM dws_trade_order_daily")
        self.assertFalse(result.empty)

    def test_discovery_uses_active_engine_schema(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE live_orders (refund_amount REAL, region_name TEXT)"))
        with patch("app.service.db_service.db_service", self.db):
            layer = SemanticLayer()
        self.assertEqual(set(layer.discovered_table_columns), {"live_orders"})
        self.assertEqual(layer.resolve_metric("refund_amount").source_table, "live_orders")

    def test_empty_physical_schema_does_not_register_demo_metrics(self):
        with patch("app.service.db_service.db_service", self.db):
            layer = SemanticLayer()
        self.assertEqual(layer.discovered_table_columns, {})
        self.assertIsNone(layer.resolve_metric("refund_amount"))

    def test_failed_physical_discovery_does_not_register_demo_metrics(self):
        with patch("app.service.db_service.db_service", self.db), patch(
            "sqlalchemy.inspect", side_effect=RuntimeError("database unavailable")
        ):
            layer = SemanticLayer()
        self.assertEqual(layer.discovered_table_columns, {})
        self.assertEqual(layer.metrics, {})

    def test_discovery_keeps_physical_views(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE VIEW order_totals AS SELECT 7.5 AS refund_amount"))
        with patch("app.service.db_service.db_service", self.db):
            layer = SemanticLayer()
        self.assertIn("order_totals", layer.discovered_table_columns)

    def test_invalid_configured_engine_does_not_start_in_demo_mode(self):
        with patch.dict(os.environ, {"DB_TYPE": "postgres", "DATABASE_URL": "invalid://private"}), patch(
            "app.service.db_service.create_engine", side_effect=RuntimeError("secret-password")
        ):
            with self.assertRaisesRegex(RuntimeError, "未切换到演示") as error:
                DBService()
        self.assertNotIn("secret-password", str(error.exception))

    def test_explicit_physical_mode_requires_a_connection_url(self):
        with patch.dict(os.environ, {"DB_TYPE": "postgres", "DATABASE_URL": "", "DB_URL": ""}), patch(
            "app.service.db_service.os.path.exists", return_value=False
        ):
            with self.assertRaisesRegex(RuntimeError, "没有配置数据库连接地址"):
                DBService()


if __name__ == "__main__":
    unittest.main()
