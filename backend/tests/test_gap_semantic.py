# -*- coding: utf-8 -*-
"""语义层补齐能力回归：口径版本化 / 预聚合优先路由 / 表名分层解析 / 半结构化列识别。

对应技术报告 §5.2 的四条差距：
  1. Metric 无 version/status，口径原地覆盖 → 历史报表无法复现；
  2. 主表硬编码 / 直取 metrics[0].source_table → 放着 ADS/DWS 汇总表不走明细表；
  3. backend 零分层感知（无 layer/domain/grain 解析）；
  4. json/jsonb/array 列被无条件 register_dimension() → 无法聚合的脏维度。
"""

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
        MetricVersionAmbiguity,
        METRIC_STATUS_ACTIVE,
        METRIC_STATUS_DEPRECATED,
        SemanticLayer,
        is_pre_aggregated_layer,
        is_semi_structured_type,
        parse_table_grain,
        parse_table_name,
        semi_structured_kind,
        table_layer_rank,
        table_profile,
    )


def make_layer() -> SemanticLayer:
    """构建一个不触发自动发现的空语义层（自动发现会连真实库）。"""
    with patch.object(SemanticLayer, "_initialize_registry"):
        return SemanticLayer()


def gmv_metric(**overrides) -> Metric:
    payload = dict(
        name="total_gmv", aliases=["gmv", "交易额"], description="fixture",
        calculation="gmv", unit="元", available_dimensions=["region_name"],
        default_agg="SUM", source_table="dwd_trade_order_di",
    )
    payload.update(overrides)
    return Metric(**payload)


# =====================================================================
# 1. 表名分层解析（layer / domain / subject / grain）
# =====================================================================
class TableNamingTests(unittest.TestCase):
    def test_layered_names_split_into_layer_domain_subject(self):
        cases = {
            "dwd_ord_order_di": ("DWD", "ord", "order"),
            "ads_trade_gmv_1d": ("ADS", "trade", "gmv"),
            "dws_audio_album_daily": ("DWS", "audio", "album"),
            "dim_store": ("DIM", "store", ""),
        }
        for table, expected in cases.items():
            with self.subTest(table=table):
                self.assertEqual(parse_table_name(table), expected)

    def test_unlayered_names_are_not_forced_into_a_layer(self):
        # 不规范表名不能被臆测成某一层，否则路由会凭空"升级"明细表。
        self.assertEqual(parse_table_name("articles"), ("", "articles", ""))
        self.assertEqual(parse_table_name("article_history"), ("", "article", "history"))
        self.assertEqual(table_profile("articles").layer, "")
        self.assertFalse(table_profile("articles").pre_aggregated)

    def test_grain_parsed_from_table_suffix(self):
        self.assertEqual(parse_table_grain("dwd_ord_order_di"), "day")
        self.assertEqual(parse_table_grain("ads_trade_gmv_mf"), "month")
        self.assertEqual(parse_table_grain("dws_trade_gmv_1h"), "hour")
        self.assertEqual(parse_table_grain("dim_store"), "")

    def test_layer_rank_prefers_summary_layers_over_detail(self):
        self.assertLess(table_layer_rank("ADS"), table_layer_rank("DWS"))
        self.assertLess(table_layer_rank("DWS"), table_layer_rank("DWD"))
        self.assertLess(table_layer_rank("DWD"), table_layer_rank("ODS"))
        # 未知分层绝不优于任何已知分层
        self.assertGreater(table_layer_rank(""), table_layer_rank("ODS"))
        self.assertTrue(is_pre_aggregated_layer("ads"))
        self.assertFalse(is_pre_aggregated_layer("DWD"))


# =====================================================================
# 2. 半结构化列识别
# =====================================================================
class SemiStructuredColumnTests(unittest.TestCase):
    def test_type_classification(self):
        semi = {
            "JSON": "json", "jsonb": "json", "ARRAY": "array", "TEXT[]": "array",
            "MAP<STRING, INT>": "map", "STRUCT<a:INT>": "struct", "hstore": "map",
        }
        for dtype, kind in semi.items():
            with self.subTest(dtype=dtype):
                self.assertTrue(is_semi_structured_type(dtype))
                self.assertEqual(semi_structured_kind(dtype), kind)
        for dtype in ("VARCHAR(64)", "INTEGER", "DATE", "TIMESTAMP", "NUMERIC(18,2)", ""):
            with self.subTest(dtype=dtype):
                self.assertFalse(is_semi_structured_type(dtype))

    def test_discovery_keeps_json_columns_out_of_dimensions(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE ods_cms_article_df ("
            " id INTEGER, title TEXT, payload JSON, attachments JSONB,"
            " view_count INTEGER, dt DATE)")
        conn.commit()

        class FakeDB:
            real_engine = None

        fake = FakeDB()
        fake.conn = conn
        layer = SemanticLayer(database=fake)

        # 半结构化列被识别并单独登记，而不是无条件注册成维度
        self.assertEqual(layer.semi_structured_columns["ods_cms_article_df"],
                         {"payload": "json", "attachments": "json"})
        self.assertNotIn("payload", layer.dimensions)
        self.assertNotIn("attachments", layer.dimensions)
        self.assertNotIn(("ods_cms_article_df", "payload"), layer.table_dimensions)
        # 普通标量列仍然照常建模
        self.assertIn("title", layer.dimensions)

        count_metric = layer.metrics["ods_cms_article_df_count"]
        self.assertNotIn("payload", count_metric.available_dimensions)
        self.assertNotIn("attachments", count_metric.available_dimensions)
        self.assertIn("title", count_metric.available_dimensions)
        sum_metric = layer.metrics["total_view_count"]
        self.assertNotIn("payload", sum_metric.available_dimensions)

        # 分层画像随发现流程一并建立
        profile = layer.profile_of("ods_cms_article_df")
        self.assertEqual((profile.layer, profile.domain, profile.grain), ("ODS", "cms", "day"))
        conn.close()

    def test_compiler_refuses_to_group_by_a_semi_structured_column(self):
        layer = make_layer()
        layer.discovered_table_columns["articles"] = [
            ("id", "INTEGER"), ("title", "TEXT"), ("content", "JSONB"), ("dt", "DATE")]
        layer.register_metric(Metric(
            name="articles_count", aliases=["文章数"], description="fixture",
            calculation="id", unit="篇", available_dimensions=["title"],
            default_agg="COUNT", source_table="articles"))
        compiler = DSLCompiler(layer=layer, dialect="doris")
        with patch.object(compiler, "_table_ref", side_effect=lambda table: table):
            dsl = {"metrics": [{"name": "articles_count"}],
                   "dimensions": [{"name": "content"}], "filters": []}
            with self.assertRaises(ValueError) as ctx:
                compiler.compile(dsl)
        self.assertIn("半结构化", str(ctx.exception))

        # 历史遗留的人工登记维度指向 json 列时同样拦截
        layer.register_dimension(Dimension(
            name="content", aliases=["正文"], source_table="articles", source_column="content"))
        with patch.object(compiler, "_table_ref", side_effect=lambda table: table):
            with self.assertRaises(ValueError):
                compiler.compile({"metrics": [{"name": "articles_count"}],
                                  "dimensions": [{"name": "content"}], "filters": []})

    def test_scalar_dimension_still_compiles(self):
        layer = make_layer()
        layer.discovered_table_columns["articles"] = [
            ("id", "INTEGER"), ("title", "TEXT"), ("content", "JSONB"), ("dt", "DATE")]
        layer.register_metric(Metric(
            name="articles_count", aliases=["文章数"], description="fixture",
            calculation="id", unit="篇", available_dimensions=["title"],
            default_agg="COUNT", source_table="articles"))
        layer.register_dimension(Dimension(
            name="title", aliases=["标题"], source_table="articles", source_column="title"))
        compiler = DSLCompiler(layer=layer, dialect="doris")
        with patch.object(compiler, "_table_ref", side_effect=lambda table: table):
            sql = compiler.compile({"metrics": [{"name": "articles_count"}],
                                    "dimensions": [{"name": "title"}], "filters": []})
        self.assertIn("articles.title", sql)


# =====================================================================
# 3. 口径版本化
# =====================================================================
class MetricVersioningTests(unittest.TestCase):
    def setUp(self):
        self.layer = make_layer()
        self.layer.register_metric(gmv_metric(version="v1", calculation="gmv"))

    def test_single_version_behaves_exactly_as_before(self):
        metric = self.layer.resolve_metric("交易额")
        self.assertIsNotNone(metric)
        self.assertEqual(metric.version, "v1")
        self.assertEqual(metric.status, METRIC_STATUS_ACTIVE)
        self.assertIs(self.layer.metrics["total_gmv"], metric)

    def test_new_version_does_not_overwrite_history_and_is_never_silently_latest(self):
        self.layer.register_metric(gmv_metric(version="v2", calculation="gmv_excl_refund"))
        # 历史版本仍在台账里，历史报表可复现
        versions = {m.version: m.calculation for m in
                    self.layer.metric_version_candidates("total_gmv")}
        self.assertEqual(versions, {"v1": "gmv", "v2": "gmv_excl_refund"})
        # 多个生效版本且无默认 → 拒绝解析（绝不静默选最新）
        self.assertIsNone(self.layer.resolve_metric("total_gmv"))
        self.assertIsNone(self.layer.resolve_metric("交易额"))
        self.assertEqual(self.layer.metric_version_ambiguity("total_gmv"), ["v1", "v2"])
        # 代表项也不会被悄悄换成新版本
        self.assertEqual(self.layer.metrics["total_gmv"].version, "v1")

    def test_default_version_resolves_and_explicit_version_reproduces_history(self):
        self.layer.register_metric(gmv_metric(version="v2", calculation="gmv_excl_refund"))
        self.layer.set_default_metric_version("total_gmv", "v2")
        self.assertEqual(self.layer.resolve_metric("total_gmv").calculation, "gmv_excl_refund")
        self.assertEqual(self.layer.metric_version_ambiguity("total_gmv"), [])
        # 显式指定旧版本 → 复现历史口径
        self.assertEqual(self.layer.resolve_metric("total_gmv", "v1").calculation, "gmv")
        self.assertEqual(self.layer.resolve_metric("交易额", "v1").version, "v1")
        self.assertIsNone(self.layer.resolve_metric("total_gmv", "v9"))

    def test_same_version_registration_is_idempotent_overwrite(self):
        self.layer.register_metric(gmv_metric(version="v1", calculation="gmv_fixed"))
        self.assertEqual(len(self.layer.metric_version_candidates("total_gmv")), 1)
        self.assertEqual(self.layer.resolve_metric("total_gmv").calculation, "gmv_fixed")

    def test_deprecated_version_leaves_resolution_unambiguous_but_reproducible(self):
        self.layer.register_metric(gmv_metric(version="v2", calculation="gmv_excl_refund"))
        self.layer.deprecate_metric_version("total_gmv", "v2")
        self.assertEqual(self.layer.resolve_metric("total_gmv").version, "v1")
        deprecated = self.layer.resolve_metric("total_gmv", "v2")
        self.assertEqual(deprecated.status, METRIC_STATUS_DEPRECATED)
        self.assertEqual(deprecated.calculation, "gmv_excl_refund")
        self.assertEqual([m.version for m in self.layer.active_metric_versions("total_gmv")],
                         ["v1"])

    def test_deprecating_the_default_version_clears_the_default(self):
        self.layer.register_metric(gmv_metric(version="v2", calculation="gmv_excl_refund"))
        self.layer.set_default_metric_version("total_gmv", "v2")
        self.layer.deprecate_metric_version("total_gmv", "v2")
        self.assertNotIn("total_gmv", self.layer.metric_default_versions)
        self.assertEqual(self.layer.resolve_metric("total_gmv").version, "v1")

    def test_fully_deprecated_metric_leaves_the_active_menu_but_stays_reproducible(self):
        self.layer.deprecate_metric_version("total_gmv", "v1")
        self.assertNotIn("total_gmv", self.layer.metrics)  # 不再被检索/推荐提议
        self.assertIsNone(self.layer.resolve_metric("total_gmv"))
        self.assertEqual(self.layer.resolve_metric("total_gmv", "v1").calculation, "gmv")

    def test_invalid_governance_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.layer.set_default_metric_version("total_gmv", "v7")
        with self.assertRaises(ValueError):
            self.layer.deprecate_metric_version("total_gmv", "v7")
        with self.assertRaises(ValueError):
            self.layer.register_metric(gmv_metric(version="v3", status="retired"))

    def test_compiler_asks_back_instead_of_picking_a_version(self):
        self.layer.register_metric(gmv_metric(version="v2", calculation="gmv_excl_refund"))
        self.layer.discovered_table_columns["dwd_trade_order_di"] = [
            ("gmv", "NUMERIC"), ("gmv_excl_refund", "NUMERIC"), ("dt", "DATE")]
        compiler = DSLCompiler(layer=self.layer, dialect="doris")
        with patch.object(compiler, "_table_ref", side_effect=lambda table: table):
            with self.assertRaises(MetricVersionAmbiguity) as ctx:
                compiler.compile({"metrics": [{"name": "total_gmv"}],
                                  "dimensions": [], "filters": []})
        self.assertEqual(ctx.exception.versions, ["v1", "v2"])
        self.assertIsInstance(ctx.exception, ValueError)  # 既有 except ValueError 调用方不受影响

    def test_compiler_pins_the_requested_version_formula(self):
        self.layer.register_metric(gmv_metric(version="v2", calculation="gmv_excl_refund"))
        self.layer.discovered_table_columns["dwd_trade_order_di"] = [
            ("gmv", "NUMERIC"), ("gmv_excl_refund", "NUMERIC"), ("dt", "DATE")]
        compiler = DSLCompiler(layer=self.layer, dialect="doris")
        with patch.object(compiler, "_table_ref", side_effect=lambda table: table):
            sql_v1 = compiler.compile({"metrics": [{"name": "total_gmv", "version": "v1"}],
                                       "dimensions": [], "filters": []})
            sql_v2 = compiler.compile({"metrics": [{"name": "total_gmv", "version": "v2"}],
                                       "dimensions": [], "filters": []})
        self.assertIn("SUM(dwd_trade_order_di.gmv)", sql_v1)
        self.assertNotIn("gmv_excl_refund", sql_v1)
        self.assertIn("gmv_excl_refund", sql_v2)

    def test_unknown_version_reports_registered_versions(self):
        compiler = DSLCompiler(layer=self.layer, dialect="doris")
        with self.assertRaises(ValueError) as ctx:
            compiler.compile({"metrics": [{"name": "total_gmv", "version": "v9"}],
                              "dimensions": [], "filters": []})
        self.assertIn("v9", str(ctx.exception))
        self.assertIn("v1", str(ctx.exception))


# =====================================================================
# 4. 预聚合优先路由
# =====================================================================
class PreAggregationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.layer = make_layer()
        self.layer.discovered_table_columns = {
            "dwd_trade_order_di": [("id", "INTEGER"), ("region_name", "TEXT"),
                                   ("gmv", "NUMERIC"), ("dt", "DATE")],
            "dws_trade_gmv_di": [("region_name", "TEXT"), ("gmv", "NUMERIC"), ("dt", "DATE")],
        }
        self.layer.register_metric(gmv_metric())
        for table in self.layer.discovered_table_columns:
            self.layer.register_dimension(Dimension(
                name="region_name", aliases=["区域"], source_table=table,
                source_column="region_name"))

    def route(self, dims=("region_name",), grain="day"):
        return self.layer.route_primary_table(
            [self.layer.resolve_metric("total_gmv")], list(dims), required_grain=grain)

    def test_summary_layer_wins_over_detail_layer(self):
        self.assertEqual(self.route(), ("dws_trade_gmv_di", "preagg"))

    def test_ads_wins_over_dws(self):
        self.layer.discovered_table_columns["ads_trade_gmv_di"] = [
            ("region_name", "TEXT"), ("gmv", "NUMERIC"), ("dt", "DATE")]
        self.assertEqual(self.route()[0], "ads_trade_gmv_di")

    def test_candidate_missing_a_requested_dimension_is_not_used(self):
        self.layer.discovered_table_columns["dws_trade_gmv_di"] = [
            ("gmv", "NUMERIC"), ("dt", "DATE")]  # 没有 region_name
        self.assertEqual(self.route(), ("dwd_trade_order_di", "base"))

    def test_coarser_grain_candidate_is_rejected_for_a_finer_request(self):
        self.layer.discovered_table_columns.pop("dws_trade_gmv_di")
        self.layer.discovered_table_columns["ads_trade_gmv_mf"] = [
            ("region_name", "TEXT"), ("gmv", "NUMERIC"), ("dt", "DATE")]
        self.assertEqual(self.route(grain="day"), ("dwd_trade_order_di", "base"))
        # 请求本身就是月粒度时才允许走月表
        self.assertEqual(self.route(grain="month"), ("ads_trade_gmv_mf", "preagg"))

    def test_candidate_without_business_time_column_is_rejected(self):
        self.layer.discovered_table_columns["dws_trade_gmv_di"] = [
            ("region_name", "TEXT"), ("gmv", "NUMERIC")]  # 无 dt，会丢失时间过滤
        self.assertEqual(self.route(), ("dwd_trade_order_di", "base"))

    def test_expression_metric_is_never_moved_across_tables(self):
        self.layer.register_metric(Metric(
            name="refund_ratio", aliases=["退款率"], description="fixture",
            calculation="refund_amount / NULLIF(gmv, 0)", unit="%",
            available_dimensions=["region_name"], default_agg="formula",
            source_table="dwd_trade_order_di"))
        result = self.layer.route_primary_table(
            [self.layer.resolve_metric("refund_ratio")], ["region_name"])
        self.assertEqual(result, ("dwd_trade_order_di", "base"))

    def test_filtered_column_missing_on_the_summary_table_keeps_the_detail_table(self):
        # 汇总表没有 goods_name 时改路由会退化成错误的兜底 JOIN，必须留在明细表。
        self.layer.discovered_table_columns["dwd_trade_order_di"].append(("goods_name", "TEXT"))
        self.layer.register_dimension(Dimension(
            name="goods_name", aliases=["商品"], source_table="dwd_trade_order_di",
            source_column="goods_name"))
        result = self.layer.route_primary_table(
            [self.layer.resolve_metric("total_gmv")], ["region_name"],
            filter_fields=["goods_name"])
        self.assertEqual(result, ("dwd_trade_order_di", "base"))
        # 时间过滤字段不参与列覆盖判断，不应该阻止改路由
        self.assertEqual(
            self.layer.route_primary_table(
                [self.layer.resolve_metric("total_gmv")], ["region_name"],
                filter_fields=["dt"]),
            ("dws_trade_gmv_di", "preagg"))

    def test_routing_can_be_switched_off(self):
        with patch.dict(os.environ, {"SEMANTIC_PREAGG_ROUTING": "0"}):
            self.assertEqual(self.route(), ("dwd_trade_order_di", "base"))

    def test_unlayered_tables_keep_the_original_source_table(self):
        layer = make_layer()
        layer.discovered_table_columns = {
            "articles": [("id", "INTEGER"), ("title", "TEXT"), ("dt", "DATE")],
            "article_history": [("id", "INTEGER"), ("title", "TEXT"), ("dt", "DATE")],
        }
        layer.register_metric(Metric(
            name="articles_count", aliases=["文章数"], description="fixture",
            calculation="id", unit="篇", available_dimensions=["title"],
            default_agg="COUNT", source_table="articles"))
        self.assertEqual(
            layer.route_primary_table([layer.resolve_metric("articles_count")], ["title"]),
            ("articles", "base"))

    def test_compiled_sql_reads_the_summary_table(self):
        compiler = DSLCompiler(layer=self.layer, dialect="doris")
        with patch.object(compiler, "_table_ref", side_effect=lambda table: table):
            sql = compiler.compile({
                "metrics": [{"name": "total_gmv"}],
                "dimensions": [{"name": "region_name"}],
                "time_range": {"start": "2026-09-01", "end": "2026-09-02"},
                "filters": []})
        expression = sqlglot.parse_one(sql, read="doris")
        tables = {table.name for table in expression.find_all(sqlglot.exp.Table)}
        self.assertEqual(tables, {"dws_trade_gmv_di"})
        self.assertNotIn("dwd_trade_order_di", sql)
        self.assertEqual(compiler.last_route["mode"], "preagg")
        self.assertEqual(compiler.last_route["base_table"], "dwd_trade_order_di")

    def test_custom_select_no_longer_falls_back_to_a_hardcoded_demo_table(self):
        compiler = DSLCompiler(layer=self.layer, dialect="doris")
        with self.assertRaises(ValueError) as ctx:
            compiler.compile({"custom_select": "1", "metrics": [{"name": "not_registered"}]})
        message = str(ctx.exception)
        self.assertNotIn("dws_trade_order_daily", message)
        self.assertIn("custom_table", message)

    def test_custom_select_still_resolves_the_table_from_a_known_metric(self):
        compiler = DSLCompiler(layer=self.layer, dialect="doris")
        sql = compiler.compile({"custom_select": "region_name, SUM(gmv)",
                                "metrics": [{"name": "total_gmv"}]})
        self.assertEqual(sql, "SELECT region_name, SUM(gmv) FROM dwd_trade_order_di")


if __name__ == "__main__":
    unittest.main()
