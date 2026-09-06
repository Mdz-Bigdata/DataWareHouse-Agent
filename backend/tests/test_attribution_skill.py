"""Regression tests for semantic attribution binding and honest query failures."""
import importlib
import os
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd
import sqlglot

# Imports bootstrap an in-memory demo database only, never a configured physical one.
with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    attribution = importlib.import_module("app.service.skills.attribution_skill")
    db_module = importlib.import_module("app.service.db_service")
    semantic = importlib.import_module("app.service.semantic_layer")
from app.service.skills.base_skill import SkillContext
from app.schema.chat import AskResponse


class FixtureDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.real_engine = None
        self.is_sample_data = True
        self.active_db_type = "sqlite"
        self.calls = []
        self.failure = None

    def get_active_db_name(self):
        return "main"

    def execute_query(self, sql, dialect="mysql"):
        self.calls.append((sql, dialect))
        if self.failure:
            raise self.failure
        translated = sqlglot.transpile(sql, read=dialect, write="sqlite")[0]
        return pd.read_sql_query(translated, self.conn)


class AttributionSkillTests(unittest.TestCase):
    def setUp(self):
        with patch.object(semantic.SemanticLayer, "_initialize_registry"):
            self.layer = semantic.SemanticLayer()
        self.db = FixtureDB()
        self.addCleanup(self.db.conn.close)
        for target, name, value in (
            (attribution, "semantic_layer", self.layer),
            (attribution, "db_service", self.db),
            (db_module, "db_service", self.db),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"DB_TYPE": "sqlite"})
        env.start()
        self.addCleanup(env.stop)
        self.skill = attribution.AttributionSkill()

    def register_table(self, name, ddl):
        self.db.conn.execute(f"CREATE TABLE {name} ({ddl})")
        rows = self.db.conn.execute(f"PRAGMA table_info({name})").fetchall()
        self.layer.discovered_table_columns[name] = [(row[1], row[2]) for row in rows]

    def register_metric(self, name="net_refunds", table="merchant_sales", column="net_refund",
                        dimension="channel", aliases=None, roles=None):
        metric = semantic.Metric(
            name=name, aliases=aliases or ["退货损失"], description="Actual business metric",
            calculation=column, unit="元", available_dimensions=[dimension],
            source_table=table, authorized_roles=roles or ["admin", "analyst", "user"],
        )
        self.layer.register_metric(metric)
        return metric

    def custom_fixture(self):
        self.register_table("merchant_sales", "net_refund REAL, channel TEXT")
        self.register_metric()
        self.layer.register_dimension(semantic.Dimension(
            name="channel", aliases=["渠道"], source_table="merchant_sales", source_column="channel"))
        self.db.conn.executemany("INSERT INTO merchant_sales VALUES (?, ?)",
                                 [(3, "门店"), (7, "门店"), (5, "线上")])

    def ask(self, question="退货损失按渠道下降的原因", **kwargs):
        return self.skill.execute(SkillContext(question=question, role="analyst", **kwargs))

    def assert_failure(self, result, no_query=True):
        self.assertFalse(result.success)
        AskResponse.model_validate(result.model_dump())
        self.assertTrue(result.error)
        self.assertEqual(result.data, [])
        self.assertIsNone(result.attribution_data)
        self.assertIsNone(result.chart)
        if no_query:
            self.assertEqual(self.db.calls, [])

    def test_missing_schema_does_not_query_or_invent_rows(self):
        self.assert_failure(self.ask("退款金额按区域下降的原因"))

    def test_unrelated_schema_does_not_fall_back_to_dws(self):
        self.custom_fixture()
        result = self.ask("退款金额按区域下降的原因")
        self.assert_failure(result)
        self.assertIn("未注册", result.error)

    def test_actual_custom_metric_table_and_requested_dialect(self):
        self.custom_fixture()
        result = self.ask(dialect="postgres")
        self.assertTrue(result.success, result.error)
        AskResponse.model_validate(result.model_dump())
        self.assertEqual(result.attribution_data["total_value"], 15)
        self.assertEqual(result.attribution_data["metric_unit"], "元")
        self.assertEqual(result.data[0]["dimension_slice"], "门店")
        self.assertEqual(result.data[0]["value"], 10)
        self.assertEqual(self.db.calls[0][1], "postgres")
        self.assertEqual(result.details["tables"], ["merchant_sales"])
        self.assertNotIn("dws_trade_order_daily", result.details["sql"])
        self.assertIn("不能据此确定涨跌原因", result.conclusion)
        self.assertIn("演示数据", result.conclusion)

    def test_full_group_total_is_not_limited_to_ten_slices(self):
        self.custom_fixture()
        self.db.conn.executemany("INSERT INTO merchant_sales VALUES (?, ?)",
                                 [(1, f"渠道{i}") for i in range(12)])
        result = self.ask()
        self.assertTrue(result.success, result.error)
        self.assertEqual(len(result.data), 14)
        self.assertEqual(result.attribution_data["total_value"], 27)
        self.assertNotIn("LIMIT", self.db.calls[0][0])

    def test_auto_discovered_total_metric_uses_compilers_result_alias(self):
        self.custom_fixture()
        metric = self.layer.metrics.pop("net_refunds")
        metric.name = "total_net_refunds"
        self.layer.register_metric(metric)
        result = self.ask()
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.attribution_data["total_value"], 15)
        self.assertNotIn("total_total_net_refunds", result.details["sql"])

    def test_dimension_is_joined_from_registered_real_region_table(self):
        self.register_table("store_refunds", "refund_amount REAL, region_id INTEGER")
        self.register_table("sales_regions", "region_id INTEGER, region_name TEXT")
        self.register_metric(table="store_refunds", column="refund_amount",
                             dimension="region_name", aliases=["退款金额"])
        self.layer.register_dimension(semantic.Dimension(
            name="region_name", aliases=["区域"], source_table="sales_regions", source_column="region_name"))
        self.layer.register_join_path(semantic.JoinPath(
            from_table="store_refunds", to_table="sales_regions",
            condition="store_refunds.region_id = sales_regions.region_id"))
        self.db.conn.executemany("INSERT INTO store_refunds VALUES (?, ?)", [(12, 1), (8, 2)])
        self.db.conn.executemany("INSERT INTO sales_regions VALUES (?, ?)", [(1, "华东"), (2, "华北")])
        result = self.ask("退款金额按区域下降的原因")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data[0]["dimension_slice"], "华东")
        self.assertEqual(result.attribution_data["total_value"], 20)
        self.assertEqual(set(result.details["tables"]), {"store_refunds", "sales_regions"})
        self.assertIn("JOIN sales_regions", result.details["sql"])

    def test_missing_dimension_or_join_does_not_guess_columns(self):
        self.custom_fixture()
        self.assert_failure(self.ask("退货损失按区域下降的原因"))
        self.layer.metrics["net_refunds"].available_dimensions.append("region_name")
        self.register_table("sales_regions", "region_name TEXT")
        self.layer.register_dimension(semantic.Dimension(
            name="region_name", aliases=["区域"], source_table="sales_regions", source_column="region_name"))
        self.assert_failure(self.ask("退货损失按区域下降的原因"))

    def test_ambiguous_metric_is_a_structured_failure(self):
        self.custom_fixture()
        self.register_metric(name="gross_refunds")
        self.assert_failure(self.ask())

    def test_stale_metric_column_is_a_structured_failure(self):
        self.custom_fixture()
        self.layer.metrics["net_refunds"].calculation = "missing_amount"
        self.assert_failure(self.ask())

    def test_denied_role_is_not_queried(self):
        self.custom_fixture()
        self.layer.metrics["net_refunds"].authorized_roles = ["admin"]
        self.assert_failure(self.ask())

    def test_database_failure_has_no_fake_results_or_raw_error(self):
        self.custom_fixture()
        self.db.failure = RuntimeError("postgresql://secret@example.invalid:5432/private missing relation")
        result = self.ask()
        self.assert_failure(result, no_query=False)
        self.assertEqual(len(self.db.calls), 1)
        self.assertIn("未生成分析结果", result.error)
        self.assertNotIn("secret", result.error)

    def test_demo_route_is_labeled_even_without_sqlite_environment(self):
        self.custom_fixture()
        with patch.dict(os.environ, {"DB_TYPE": "postgres"}):
            result = self.ask()
        self.assertTrue(result.success, result.error)
        self.assertIn("演示数据", result.conclusion)
        self.assertEqual(result.details["data_source"], "demo")

    def test_auto_registered_rate_is_not_summed_into_attribution(self):
        self.register_table("audio_activity", "completion_rate REAL, category_name TEXT")
        self.register_metric(name="total_completion_rate", table="audio_activity", column="completion_rate",
                             dimension="category_name", aliases=["完播率"])
        self.layer.register_dimension(semantic.Dimension(
            name="category_name", aliases=["品类"], source_table="audio_activity", source_column="category_name"))
        self.assert_failure(self.ask("听书完播率按品类下降的原因"))

    def test_zero_and_empty_results_keep_numeric_frontend_contract(self):
        self.custom_fixture()
        self.db.conn.execute("UPDATE merchant_sales SET net_refund = 0")
        result = self.ask()
        self.assertTrue(result.success, result.error)
        AskResponse.model_validate(result.model_dump())
        self.assertEqual(result.attribution_data["top_driver_ratio"], 0.0)
        self.assertTrue(all(row["contribution_rate"] == "—" for row in result.data))
        self.assertTrue(all(item["ratio"] == 0.0 for item in result.attribution_data["waterfall_items"]))
        self.db.conn.execute("DELETE FROM merchant_sales")
        result = self.ask()
        self.assertTrue(result.success, result.error)
        AskResponse.model_validate(result.model_dump())
        self.assertEqual(result.data, [])
        self.assertEqual(result.attribution_data["top_driver_ratio"], 0.0)

    def test_audio_metric_binds_to_discovered_business_table(self):
        self.register_table("membership_revenue", "income REAL, plan_name TEXT")
        self.register_metric(name="member_income", table="membership_revenue", column="income",
                             dimension="plan_name", aliases=["听书会员收入"])
        self.layer.register_dimension(semantic.Dimension(
            name="plan_name", aliases=["套餐"], source_table="membership_revenue", source_column="plan_name"))
        self.db.conn.execute("INSERT INTO membership_revenue VALUES (12, '月卡')")
        result = self.ask("听书会员收入按套餐下降原因")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.attribution_data["total_value"], 12)
        self.assertEqual(result.details["tables"], ["membership_revenue"])

    def dated_fixture(self, rows):
        self.register_table("merchant_sales", "dt TEXT, net_refund REAL, channel TEXT")
        self.register_metric()
        self.layer.register_dimension(semantic.Dimension(
            name="channel", aliases=["渠道"], source_table="merchant_sales", source_column="channel"))
        self.db.conn.executemany("INSERT INTO merchant_sales VALUES (?, ?, ?)", rows)

    def test_period_comparison_aligns_added_removed_and_null_slices(self):
        self.dated_fixture([("2026-07-30", 10, "消失渠道"), ("2026-07-31", 5, None),
                            ("2026-08-01", 20, "新增渠道"), ("2026-08-02", 8, None)])
        result = self.ask("2026-08-01至2026-08-02退货损失按渠道归因")
        self.assertTrue(result.success, result.error)
        AskResponse.model_validate(result.model_dump())
        analysis = result.attribution_data
        self.assertEqual(analysis["baseline_value"], 15)
        self.assertEqual(analysis["current_value"], 28)
        self.assertEqual(analysis["total_change"], 13)
        deltas = {item["name"]: item["value"] for item in analysis["waterfall_items"]}
        self.assertEqual(deltas, {"消失渠道": -10, "新增渠道": 20, "未分类（空值）": 3})
        self.assertEqual(len(self.db.calls), 2)

    def test_zero_net_change_and_zero_baseline_have_defined_values(self):
        self.dated_fixture([("2026-07-30", 10, "门店"), ("2026-07-31", 5, "线上"),
                            ("2026-08-01", 7, "门店"), ("2026-08-02", 8, "线上")])
        result = self.ask("2026-08-01至2026-08-02退货损失按渠道归因")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.attribution_data["total_change"], 0)
        self.assertTrue(all(row["contribution_rate"] == "—" for row in result.data))
        self.db.conn.execute("UPDATE merchant_sales SET net_refund = 0 WHERE dt < '2026-08-01'")
        result = self.ask("2026-08-01至2026-08-02退货损失按渠道归因")
        self.assertTrue(result.success, result.error)
        self.assertIsNone(result.attribution_data["change_rate"])


if __name__ == "__main__":
    unittest.main()
