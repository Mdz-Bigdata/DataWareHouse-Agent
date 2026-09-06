"""Regressions for audio metric aliases and calendar/timestamp SQL bounds."""

import os
import sqlite3
import unittest
from unittest.mock import patch

import sqlglot

with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    from app.service.semantic_layer import (
        DSLCompiler,
        Dimension,
        Metric,
        SemanticLayer,
        TIMEZONE_CONFIG,
    )


class AudioSQLCompilerTests(unittest.TestCase):
    def setUp(self):
        with patch.object(SemanticLayer, "_initialize_registry"):
            self.layer = SemanticLayer()
        self.layer.register_metric(Metric(
            name="total_play_count", aliases=["play_count", "播放量"],
            description="Fixture playback count", calculation="play_count", unit="次",
            available_dimensions=["category_name"], source_table="audio_daily",
        ))
        self.layer.register_dimension(Dimension(
            name="category_name", aliases=["分类"],
            source_table="audio_daily", source_column="category_name",
        ))
        self.set_time_column("dt", "DATE")
        self.compiler = DSLCompiler(layer=self.layer, dialect="doris")
        table_ref = patch.object(self.compiler, "_table_ref", side_effect=lambda table: table)
        table_ref.start()
        self.addCleanup(table_ref.stop)
        timezone_config = patch.dict(TIMEZONE_CONFIG, {
            "business": "Asia/Shanghai", "database": "America/Chicago",
        })
        timezone_config.start()
        self.addCleanup(timezone_config.stop)

    def set_time_column(self, name, sql_type):
        self.layer.discovered_table_columns["audio_daily"] = [
            (name, sql_type), ("category_name", "TEXT"), ("play_count", "INTEGER"),
        ]

    def query(self, metric="play_count", order_by=None, use_time_range=True):
        dsl = {
            "metrics": [{"name": metric}], "dimensions": [{"name": "category_name"}],
            "filters": [{"field": "dt", "op": "between",
                         "value": ["2026-09-04", "2026-09-04"]}],
        }
        if use_time_range:
            dsl["time_range"] = {"start": "2026-09-04", "end": "2026-09-04"}
        if order_by is not None:
            dsl["order_by"] = order_by
        sql = self.compiler.compile(dsl)
        return sql, sqlglot.parse_one(sql, read="doris")

    def assert_canonical_metric_sort(self, expression, descending=True):
        self.assertEqual(expression.expressions[-1].alias, "total_play_count")
        order = expression.args["order"].expressions[0]
        self.assertEqual(order.this.name, "total_play_count")
        self.assertEqual(bool(order.args.get("desc")), descending)

    def test_canonical_metric_does_not_gain_duplicate_total_prefix(self):
        sql, expression = self.query(metric="total_play_count")
        self.assertNotIn("total_total_play_count", sql)
        self.assert_canonical_metric_sort(expression)

    def test_metric_alias_uses_same_selected_and_default_sorted_column(self):
        _, expression = self.query(metric="play_count")
        self.assert_canonical_metric_sort(expression)

    def test_explicit_sort_resolves_alias_and_canonical_metric_name(self):
        for field in ("play_count", "total_play_count", "播放量"):
            with self.subTest(field=field):
                _, expression = self.query(order_by=[{"field": field, "direction": "asc"}])
                self.assert_canonical_metric_sort(expression, descending=False)

    def test_base_metric_sort_keeps_first_projection_when_mom_is_also_selected(self):
        for sort_field in (None, "play_count", "total_play_count"):
            with self.subTest(sort_field=sort_field):
                dsl = {
                    "metrics": [
                        {"name": "total_play_count"},
                        {"name": "total_play_count", "ratio_type": "mom"},
                    ],
                    "dimensions": [{"name": "category_name"}],
                    "time_range": {"start": "2026-09-04", "end": "2026-09-04"},
                }
                if sort_field is not None:
                    dsl["order_by"] = [{"field": sort_field, "direction": "desc"}]
                expression = sqlglot.parse_one(self.compiler.compile(dsl), read="doris")
                self.assertEqual(
                    [item.alias for item in expression.expressions[1:]],
                    ["total_play_count", "total_play_count_mom"],
                )
                order = expression.args["order"].expressions[0]
                self.assertEqual(order.this.name, "total_play_count")

    def test_legacy_layer_without_discovered_columns_retains_date_bounds(self):
        del self.layer.discovered_table_columns
        _, expression = self.query()
        between = expression.find(sqlglot.exp.Between)
        self.assertEqual(between.this.name, "dt")
        self.assertEqual(between.args["low"].this, "2026-09-04")
        self.assertEqual(between.args["high"].this, "2026-09-04")

    def test_date_partition_query_excludes_previous_and_following_day(self):
        sql, _ = self.query()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE audio_daily (dt DATE, category_name TEXT, play_count INTEGER)")
        conn.executemany("INSERT INTO audio_daily VALUES (?, ?, ?)", [
            ("2026-09-03", "回归", 90001), ("2026-09-04", "回归", 55),
            ("2026-09-05", "回归", 90002),
        ])
        sqlite_sql = sqlglot.transpile(sql, read="doris", write="sqlite")[0]
        self.assertEqual(conn.execute(sqlite_sql).fetchall(), [("回归", 55)])

    def test_date_filter_without_time_range_preserves_calendar_date(self):
        _, expression = self.query(use_time_range=False)
        between = expression.find(sqlglot.exp.Between)
        self.assertEqual(between.args["low"].this, "2026-09-04")
        self.assertEqual(between.args["high"].this, "2026-09-04")

    def test_timestamp_columns_keep_full_timezone_converted_bounds(self):
        for column, sql_type in (("dt", "TIMESTAMP"), ("dt", "DATETIME"),
                                 ("publish_time", "TIMESTAMP")):
            with self.subTest(column=column, sql_type=sql_type):
                self.set_time_column(column, sql_type)
                _, expression = self.query()
                between = expression.find(sqlglot.exp.Between)
                self.assertEqual(between.this.name, column)
                self.assertEqual(between.args["low"].this, "2026-09-03 11:00:00")
                self.assertEqual(between.args["high"].this, "2026-09-04 10:59:59")


if __name__ == "__main__":
    unittest.main()
