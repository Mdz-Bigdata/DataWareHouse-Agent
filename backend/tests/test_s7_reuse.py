# -*- coding: utf-8 -*-
"""
§7.8 复用而非重建 / §7.9-5 draft 不对生产可见 / §7.9-6 截断必须声明。

判定记录见 backend/docs/table-routing-reuse-decision.md。
本文件里的 TestEngineParserConformance 是那份判定的「契约测试」：
按文件路径加载 apps/data-agent-engine 的 table_naming.py 比对行为，
只在测试期加载，不给 backend 引入运行时依赖。
"""
import importlib.util
import os
import unittest
from pathlib import Path

from app.service.semantic_layer import (
    DEFAULT_ROW_LIMIT,
    LIMIT_SOURCE_DEFAULT,
    LIMIT_SOURCE_DSL,
    LIMIT_SOURCE_EXECUTOR,
    METRIC_STATUS_ACTIVE,
    METRIC_STATUS_DEPRECATED,
    METRIC_STATUS_DRAFT,
    DSLCompiler,
    DraftMetricNotPublished,
    LAYER_PREFIXES,
    Metric,
    SemanticLayer,
    describe_truncation,
    parse_table_grain,
    parse_table_name,
    split_probe_rows,
)

ENGINE_TABLE_NAMING = (Path(__file__).resolve().parents[2]
                       / "apps" / "data-agent-engine" / "backend" / "core" / "table_naming.py")


def _load_engine_parser():
    """按文件路径加载引擎解析器（仅测试期，不产生运行时依赖）。"""
    if not ENGINE_TABLE_NAMING.exists():
        return None
    spec = importlib.util.spec_from_file_location("_engine_table_naming", ENGINE_TABLE_NAMING)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubDB:
    """不连任何真实库的最小 db_service 替身：语义层只读 conn/real_engine。"""
    real_engine = None
    conn = None
    query_schemas = None
    active_db_type = "sqlite"
    is_sample_data = True

    def get_active_db_name(self):
        return ""


def _empty_layer() -> SemanticLayer:
    """空语义层：自动发现会失败并被吞掉，得到一个干净的注册中心。"""
    layer = SemanticLayer(database=_StubDB())
    layer.metrics.clear()
    layer.metric_versions.clear()
    layer.metric_default_versions.clear()
    layer.dimensions.clear()
    layer.table_dimensions.clear()
    layer.join_paths.clear()
    return layer


def _metric(name="gmv", version="v1", status=METRIC_STATUS_ACTIVE, table="dws_trade_daily"):
    return Metric(name=name, aliases=[name], description="", calculation="gmv",
                  unit="元", available_dimensions=[], default_agg="SUM",
                  source_table=table, version=version, status=status)


# =====================================================================
# §7.8：backend 与 engine 两份解析器的行为契约
# =====================================================================
class TestEngineParserConformance(unittest.TestCase):
    """
    两份实现是有意保留的（见 docs/table-routing-reuse-decision.md），
    但「命名规范」这份共享约定不许漂移：
    交集输入必须一致，差异必须是文档登记过的那几条。
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = _load_engine_parser()

    def setUp(self):
        if self.engine is None:
            self.skipTest("apps/data-agent-engine 不存在，跳过跨应用契约比对")

    def test_shared_convention_agrees(self):
        """两边都定义良好的输入：解析结果必须逐字相同。"""
        shared = ["dwd_ord_order_di", "ads_trade_gmv_1d", "dws_trade_order_daily",
                  "dim_store", "dim_region", "ods_log_click_di", "articles",
                  "users", "ODS_LOG_X", ""]
        for table in shared:
            with self.subTest(table=table):
                self.assertEqual(parse_table_name(table),
                                 self.engine.parse_table_name(table),
                                 f"命名规范解析漂移：{table}")

    def test_registered_difference_d1_extra_layers(self):
        """D1：backend 多认 DWT/DM 两个分层前缀，引擎会把它们当普通表名。"""
        self.assertIn("DWT", LAYER_PREFIXES)
        self.assertIn("DM", LAYER_PREFIXES)
        self.assertEqual(parse_table_name("dm_trade_gmv"), ("DM", "trade", "gmv"))
        self.assertEqual(parse_table_name("dwt_user_active_di"), ("DWT", "user", "active"))
        # 引擎当前把 dm/dwt 误当业务域——这是已登记的待修差异，改好后本断言要同步更新。
        self.assertEqual(self.engine.parse_table_name("dm_trade_gmv"), ("", "dm", "trade"))

    def test_registered_difference_d2_d3_hygiene(self):
        """D2/D3：backend 过滤空段、且对 None 不炸。"""
        self.assertEqual(parse_table_name("__a"), ("", "a", ""))
        self.assertEqual(self.engine.parse_table_name("__a"), ("", "", ""))
        self.assertEqual(parse_table_name(None), ("", "", ""))
        with self.assertRaises(AttributeError):
            self.engine.parse_table_name(None)

    def test_registered_difference_d4_grain_is_backend_only(self):
        """D4：粒度只有 backend 从表名推断（引擎靠 YAML 声明），且未知粒度不臆测。"""
        self.assertEqual(parse_table_grain("ads_trade_gmv_1d"), "day")
        self.assertEqual(parse_table_grain("dws_trade_monthly"), "month")
        self.assertEqual(parse_table_grain("dim_store"), "")
        self.assertFalse(hasattr(self.engine, "parse_table_grain"))


class TestReuseDecisionIsDocumented(unittest.TestCase):
    """判定必须留档，否则下一个人还要重做一遍这个比对。"""

    def test_decision_doc_exists_and_covers_both_sides(self):
        doc = (Path(__file__).resolve().parents[1]
               / "docs" / "table-routing-reuse-decision.md")
        self.assertTrue(doc.exists(), "§7.8 判定记录缺失")
        text = doc.read_text(encoding="utf-8")
        for token in ("table_naming.py", "_select_table_multi", "route_primary_table",
                      "authority", "granularities"):
            self.assertIn(token, text, f"判定记录未覆盖 {token}")


# =====================================================================
# §7.9-5：draft 口径不对生产可见
# =====================================================================
class TestDraftMetricsStayOutOfProduction(unittest.TestCase):

    def test_draft_only_metric_not_in_active_registry(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_DRAFT))
        self.assertNotIn("gmv", layer.metrics, "草稿口径进了在用指标清单 = 对生产可见")
        self.assertNotIn("gmv", layer.production_metrics())
        self.assertTrue(layer.is_draft_only_metric("gmv"))
        self.assertEqual([m.version for m in layer.draft_metric_versions("gmv")], ["v2"])

    def test_draft_never_resolved_implicitly(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_DRAFT))
        self.assertIsNone(layer.resolve_metric("gmv"))
        self.assertIsNone(layer.resolve_metric_version("gmv"))
        # 台账仍在，指标"存在但未发布"，报错才能说清楚
        self.assertEqual(layer.canonical_metric_name("gmv"), "gmv")

    def test_draft_resolvable_only_when_explicitly_requested(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_DRAFT))
        explicit = layer.resolve_metric("gmv", "v2")
        self.assertIsNotNone(explicit)
        self.assertEqual(explicit.status, METRIC_STATUS_DRAFT)

    def test_active_version_unaffected_by_sibling_draft(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v1", METRIC_STATUS_ACTIVE))
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_DRAFT))
        resolved = layer.resolve_metric("gmv")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.version, "v1", "草稿不得参与默认定版")
        self.assertEqual(layer.metrics["gmv"].version, "v1")
        # 草稿与生效版本并存时，不构成"多版本歧义"，不该去反问用户
        self.assertEqual(layer.metric_version_ambiguity("gmv"), [])

    def test_publish_promotes_draft(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_DRAFT))
        published = layer.publish_metric_version("gmv", "v2")
        self.assertEqual(published.status, METRIC_STATUS_ACTIVE)
        self.assertIn("gmv", layer.metrics)
        self.assertEqual(layer.resolve_metric("gmv").version, "v2")

    def test_publish_refuses_deprecated_version(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v1", METRIC_STATUS_DEPRECATED))
        with self.assertRaises(ValueError):
            layer.publish_metric_version("gmv", "v1")

    def test_multi_active_ambiguity_still_raises(self):
        """既有行为不许被 draft 改动带偏：两个生效版本仍必须反问。"""
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v1", METRIC_STATUS_ACTIVE))
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_ACTIVE))
        self.assertEqual(sorted(layer.metric_version_ambiguity("gmv")), ["v1", "v2"])
        self.assertIn("gmv", layer.metrics, "歧义时仍需保留代表项供检索/列举")

    def test_compiler_rejects_draft_only_metric_with_clear_message(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv", "v2", METRIC_STATUS_DRAFT))
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        with self.assertRaises(DraftMetricNotPublished) as ctx:
            compiler.compile({"metrics": [{"name": "gmv"}]})
        self.assertIn("草稿", str(ctx.exception))
        self.assertIn("v2", str(ctx.exception))

    def test_invalid_status_still_rejected(self):
        layer = _empty_layer()
        with self.assertRaises(ValueError):
            layer.register_metric(_metric("gmv", "v1", "whatever"))


# =====================================================================
# §7.9-6：截断必须在答案里声明
# =====================================================================
class TestTruncationIsDeclared(unittest.TestCase):

    def test_known_total_declares_exact_counts(self):
        notice = describe_truncation(10, 10, LIMIT_SOURCE_DEFAULT, total_rows=43)
        self.assertTrue(notice.truncated)
        self.assertFalse(notice.uncertain)
        self.assertIn("43", notice.message)
        self.assertIn("10", notice.message)
        self.assertIn("截断", notice.message)

    def test_unknown_total_says_maybe_not_definitely(self):
        """没探测总量时只能说"可能"——不许把猜测说成事实。"""
        notice = describe_truncation(10, 10, LIMIT_SOURCE_DEFAULT)
        self.assertFalse(notice.truncated)
        self.assertTrue(notice.uncertain)
        self.assertIn("可能", notice.message)

    def test_no_truncation_no_noise(self):
        notice = describe_truncation(3, 10, LIMIT_SOURCE_DSL)
        self.assertFalse(notice.truncated)
        self.assertFalse(notice.uncertain)
        self.assertEqual(notice.message, "")
        self.assertEqual(notice.apply_to("结论"), "结论")

    def test_executor_cap_has_its_own_wording(self):
        notice = describe_truncation(1000, 1000, LIMIT_SOURCE_EXECUTOR)
        self.assertTrue(notice.uncertain)
        self.assertIn("执行器行数上限", notice.message)

    def test_notice_prefixes_conclusion(self):
        notice = describe_truncation(10, 10, LIMIT_SOURCE_DEFAULT, total_rows=99)
        merged = notice.apply_to("各分类文章数如下。")
        self.assertTrue(merged.startswith("⚠️"))
        self.assertIn("各分类文章数如下。", merged)

    def test_compile_records_default_limit(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        sql = compiler.compile({"metrics": [{"name": "gmv"}]})
        self.assertIn(f"LIMIT {DEFAULT_ROW_LIMIT}", sql.upper())
        self.assertEqual(compiler.last_limit["limit"], DEFAULT_ROW_LIMIT)
        self.assertEqual(compiler.last_limit["limit_source"], LIMIT_SOURCE_DEFAULT)
        self.assertFalse(compiler.last_limit["probe"])

    def test_compile_records_explicit_limit(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        compiler.compile({"metrics": [{"name": "gmv"}], "limit": 5})
        self.assertEqual(compiler.last_limit["limit"], 5)
        self.assertEqual(compiler.last_limit["limit_source"], LIMIT_SOURCE_DSL)

    def test_default_compile_sql_is_unchanged_by_probe_feature(self):
        """向后兼容：不开 probe 时 SQL 文本里仍是原来的 LIMIT。"""
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        plain = compiler.compile({"metrics": [{"name": "gmv"}], "limit": 7})
        self.assertIn("LIMIT 7", plain.upper())
        self.assertNotIn("LIMIT 8", plain.upper())

    def test_probe_mode_fetches_one_extra_row(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        sql, info = compiler.compile_with_probe({"metrics": [{"name": "gmv"}], "limit": 7})
        self.assertIn("LIMIT 8", sql.upper())
        self.assertEqual(info["limit"], 7)
        self.assertTrue(info["probe"])

    def test_probe_turns_maybe_into_certain(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        compiler.compile_with_probe({"metrics": [{"name": "gmv"}], "limit": 10})
        notice = compiler.truncation_notice(11)  # 拿到 11 行 = 确认还有更多
        self.assertTrue(notice.truncated)
        self.assertFalse(notice.uncertain)
        self.assertEqual(notice.returned_rows, 10)
        self.assertIn("截断", notice.message)
        # 探测只知道"多于 10 行"，不知道具体多少，绝不谎报总数
        self.assertIsNone(notice.total_rows)

    def test_probe_exactly_full_page_is_not_truncated(self):
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        compiler.compile_with_probe({"metrics": [{"name": "gmv"}], "limit": 10})
        notice = compiler.truncation_notice(10)  # 正好 10 行 = 没有下一行
        self.assertFalse(notice.truncated)
        self.assertFalse(notice.uncertain, "探测过就不该再说『可能』")
        self.assertEqual(notice.message, "")

    def test_split_probe_rows_trims_the_extra_row(self):
        rows, truncated = split_probe_rows(list(range(11)), 10)
        self.assertEqual(len(rows), 10)
        self.assertTrue(truncated)
        rows, truncated = split_probe_rows(list(range(4)), 10)
        self.assertEqual(len(rows), 4)
        self.assertFalse(truncated)
        rows, truncated = split_probe_rows(list(range(4)), 0)
        self.assertEqual(len(rows), 4)
        self.assertFalse(truncated)

    def test_last_limit_reset_between_compiles(self):
        """上一次编译的上限不许被当成本次的（custom_select 不带 LIMIT）。"""
        layer = _empty_layer()
        layer.register_metric(_metric("gmv"))
        layer.discovered_table_columns = {"dws_trade_daily": [("gmv", "bigint")]}
        compiler = DSLCompiler(layer=layer, dialect="mysql")
        compiler.compile({"metrics": [{"name": "gmv"}], "limit": 5})
        compiler.compile({"custom_select": "COUNT(*)", "custom_table": "dws_trade_daily"})
        self.assertEqual(compiler.last_limit["limit"], 0)
        self.assertEqual(compiler.truncation_notice(1000).message, "")

    def test_executor_cap_constant_matches_engine(self):
        from app.service.semantic_layer import EXECUTOR_MAX_ROWS
        engine_executor = (Path(__file__).resolve().parents[2] / "apps" / "data-agent-engine"
                           / "backend" / "core" / "executor.py")
        if not engine_executor.exists():
            self.skipTest("引擎不存在")
        if os.getenv("EXECUTOR_MAX_ROWS"):
            self.skipTest("环境覆盖了 EXECUTOR_MAX_ROWS")
        text = engine_executor.read_text(encoding="utf-8")
        self.assertIn(f"MAX_ROWS = {EXECUTOR_MAX_ROWS}", text,
                      "backend 声明的执行器行数上限与引擎 MAX_ROWS 漂移了")


if __name__ == "__main__":
    unittest.main()
