"""Offline HTTP regressions execute the demo SQL, including both attribution periods."""
import os
import importlib
import unittest
from datetime import date
from unittest.mock import patch

os.environ["DB_TYPE"] = "sqlite"

from fastapi.testclient import TestClient
from app.main import app
from app.model.user_memory import user_memory
from app.service.ask_agent import ask_agent
from app.service.date_ranges import question_periods
from app.service.db_service import DBService
from app.service.semantic_cache import semantic_cache


class DemoWorkflowTests(unittest.TestCase):
    def setUp(self):
        # The legacy golden-runner mutates singletons while being imported by
        # unittest discovery. Bind a fresh warehouse for each HTTP scenario.
        self.db = DBService()
        self.addCleanup(self.db.conn.close)
        db_module = importlib.import_module("app.service.db_service")
        ask_module = importlib.import_module("app.service.ask_agent")
        attribution_module = importlib.import_module("app.service.skills.attribution_skill")
        semantic_module = importlib.import_module("app.service.semantic_layer")
        for target in (db_module, ask_module, attribution_module):
            patcher = patch.object(target, "db_service", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        layer = semantic_module.SemanticLayer()
        for target in (semantic_module, attribution_module, ask_agent):
            patcher = patch.object(target, "semantic_layer", layer)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for patcher in (patch.dict(os.environ, {"MOCK_LLM": "false"}),
                        patch.object(user_memory, "_save"),
                        patch.object(ask_agent, "_call_llm", side_effect=AssertionError("LLM must not be used"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        semantic_cache.invalidate_all()
        self.addCleanup(semantic_cache.invalidate_all)
        ask_agent.user_sessions.pop("demo-regression", None)
        ask_agent.user_history_questions.pop("demo-regression", None)

    def ask(self, question, role="user", dialect="doris"):
        response = self.client.post("/api/chat/ask", json={"question": question, "role": role,
                                                          "user": "demo-regression", "dialect": dialect})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_source_and_health_expose_actual_demo_mode(self):
        self.assertEqual(self.client.get("/api/chat/data-source").json()["mode"], "demo")
        self.assertEqual(self.client.get("/health").json()["data_source"], "demo")

    def test_refund_attribution_reconciles_actual_period_queries(self):
        result = self.ask("过去30天各品类退款额为什么上升")
        self.assertTrue(result["success"], result["error"])
        details, analysis = result["details"], result["attribution_data"]
        self.assertEqual(details["data_source"], "demo")
        self.assertEqual(analysis["analysis_type"], "period_comparison")
        self.assertEqual(analysis["metric_unit"], "元")
        self.assertEqual({item["name"] for item in analysis["waterfall_items"]}, {"数码3C", "生鲜食品"})
        self.assertAlmostEqual(sum(item["value"] for item in analysis["waterfall_items"]), analysis["total_change"])
        self.assertAlmostEqual(sum(item["ratio"] for item in analysis["waterfall_items"]), 100)
        for period_name, total_name in (("current_period", "current_value"), ("baseline_period", "baseline_value")):
            period = analysis[period_name]
            total = self.db.conn.execute(
                "SELECT SUM(refund_amount) FROM dws_trade_order_daily WHERE region_id = '1' AND dt BETWEEN ? AND ?",
                (period["start"], period["end"])).fetchone()[0]
            self.assertAlmostEqual(total, analysis[total_name])
        self.assertEqual(details["sql"].count("region_name = '华东'"), 2)
        self.assertIn("不代表因果证明", result["conclusion"])

    def test_audio_member_domain_uses_its_own_table(self):
        result = self.ask("最近30天听书会员退款为什么上涨")
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(result["attribution_data"]["metric_name"], "total_audio_refund_amount")
        self.assertIn("dws_audio_member_trade_daily", result["details"]["tables"])
        self.assertNotIn("dws_trade_order_daily", result["details"]["sql"])

    def test_yesterday_audio_counts_and_repeat_cache_are_valid(self):
        question = "昨天听书各分类播放量是多少"
        result = self.ask(question)
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(sum(row["total_play_count"] for row in result["data"]), 421000)
        repeated = self.ask(question)
        self.assertTrue(repeated["cache_hit"])
        self.assertEqual(repeated["data"], result["data"])

    def test_explicit_article_history_table_wins_over_shared_count_alias(self):
        first = self.ask("帮我统计一下articles 有多少篇文章")
        self.assertTrue(first["success"], first["error"])
        result = self.ask("帮我分析article_history表里按分类(category_name)分别有多少篇")
        self.assertTrue(result["success"], result["error"])
        self.assertIn("article_history", result["details"]["tables"])
        self.assertNotIn("articles", result["details"]["tables"])
        self.assertTrue(all("total_article_history_count" in row for row in result["data"]))

    def test_article_source_and_category_resolve_through_the_categories_join(self):
        result = self.ask("articles表按来源分别有多少篇文章")
        self.assertTrue(result["success"], result["error"])
        self.assertTrue(all("source_platform" in row for row in result["data"]))
        by_category = self.ask("articles表按分类分别有多少篇文章")
        self.assertTrue(by_category["success"], by_category["error"])
        self.assertIn("categories", by_category["details"]["sql"])
        self.assertEqual({row["category_name"]: row["total_articles_count"]
                          for row in by_category["data"]},
                         {"数据治理": 4, "技术架构": 3, "算法模型": 3, "行业实践": 2})

    def test_dimension_outside_the_article_domain_clarifies_without_querying(self):
        with patch.object(self.db, "execute_query", wraps=self.db.execute_query) as execute:
            unavailable = self.ask("articles表按主播分别有多少篇文章")
        self.assertFalse(unavailable["success"])
        self.assertTrue(unavailable["clarification"]["need_clarification"])
        self.assertIn("category_name", unavailable["clarification"]["message"])
        self.assertNotIn("语义审计拦截", unavailable["error"])
        execute.assert_not_called()

    def test_audio_request_after_articles_keeps_audio_domain(self):
        self.ask("articles表按来源分别有多少篇文章")
        result = self.ask("昨天听书各分类播放量是多少")
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(sum(row["total_play_count"] for row in result["data"]), 421000)
        self.assertNotIn("articles", result["details"]["tables"])

    def test_recommendations_do_not_mix_article_metric_with_audio_or_sensitive_dimension(self):
        profile = {"common_metrics": [{"metric": "articles_count"}],
                   "common_dimensions": [{"dimension": "anchor_name"}, {"dimension": "phone"}]}
        with patch.object(user_memory, "get_preference_profile", return_value=profile):
            suggestions = user_memory.get_active_recommendations("demo-regression")
        self.assertTrue(suggestions)
        for suggestion in suggestions:
            self.assertIn("articles_count", suggestion)
            # The article domain owns category_name through its categories join,
            # while audio-only and sensitive dimensions stay out of suggestions.
            self.assertIn("category_name", suggestion)
            self.assertNotIn("anchor_name", suggestion)
            self.assertNotIn("phone", suggestion)
            self.assertNotIn("退款", suggestion)
            result = self.ask(suggestion)
            self.assertTrue(result["success"], (suggestion, result["error"]))

    def test_article_private_column_remains_guarded(self):
        result = self.ask("articles表的articles_count按phone分组")
        self.assertFalse(result["success"])
        self.assertIn("敏感", result["error"])
        self.assertFalse(result.get("clarification"))

    def test_explicit_audio_table_name_does_not_add_album_grouping(self):
        result = self.ask("dws_audio_album_daily表昨天按category_name统计play_count")
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(set(result["data"][0]), {"category_name", "total_play_count"})

    def test_postgres_project_fixture_keeps_local_parsing_and_period_sql(self):
        # Exercise the physical-engine branch with PostgreSQL SQL while a local
        # fixture executes its translation; deployment covers a real server.
        import pandas as pd
        import sqlglot
        from types import SimpleNamespace
        from sqlalchemy.engine import make_url
        connection = self.db.conn

        def execute(sql, dialect):
            self.assertEqual(dialect, "postgres")
            translated = sqlglot.transpile(sql, read=dialect, write="sqlite")[0]
            return pd.read_sql_query(translated, connection)

        engine = SimpleNamespace(url=make_url("postgresql://fixture@localhost/fixture"))
        with patch.object(self.db, "real_engine", engine), patch.object(self.db, "active_db_type", "postgres"), \
                patch.object(self.db, "_has_project_fixture", True), patch.object(self.db, "conn", None), \
                patch.object(self.db, "execute_query", side_effect=execute) as queried:
            articles = self.ask("article_history表按category_name分别有多少篇", dialect="postgres")
            audio = self.ask("昨天听书各分类播放量是多少", dialect="postgres")
            refund = self.ask("过去30天各品类退款额变化归因", dialect="postgres")
        for result in (articles, audio, refund):
            self.assertTrue(result["success"], result["error"])
            self.assertIn("项目示例数据", result["conclusion"])
            self.assertNotIn("SQLite", result["conclusion"])
        self.assertEqual(queried.call_count, 4)
        self.assertEqual(sum(row["total_play_count"] for row in audio["data"]), 421000)
        self.assertEqual(refund["attribution_data"]["analysis_type"], "period_comparison")
        self.assertEqual(refund["details"]["data_source"], "configured")

    def test_query_changes_when_source_rows_change(self):
        question = "昨天华东退款金额是多少"
        original = self.ask(question)
        current, _ = question_periods(question)
        try:
            self.db.conn.execute("UPDATE dws_trade_order_daily SET refund_amount = refund_amount + 17 WHERE dt = ? AND region_id = '1'", (current["start"],))
            semantic_cache.invalidate_all()
            changed = self.ask(question)
            self.assertAlmostEqual(changed["data"][0]["total_refund_amount"], original["data"][0]["total_refund_amount"] + 34)
        finally:
            self.db.conn.execute("UPDATE dws_trade_order_daily SET refund_amount = refund_amount - 17 WHERE dt = ? AND region_id = '1'", (current["start"],))
            semantic_cache.invalidate_all()

    def test_denied_region_remains_denied(self):
        result = self.ask("昨天华北退款金额是多少")
        self.assertFalse(result["success"])
        self.assertIn("无权", result["error"])

    def test_unknown_metric_and_ratio_need_clarification(self):
        for question in ("昨天食堂消费是多少", "各品类退款额除以交易额的比率", "昨天听书会员退款率是多少"):
            result = self.ask(question)
            self.assertFalse(result["success"])
            self.assertTrue(result["clarification"]["need_clarification"])
            self.assertIsNone(result["data"])

    def test_rate_attribution_never_substitutes_an_amount(self):
        for question in ("过去30天听书会员退款率为什么上涨", "过去30天退款率归因", "听书退款金额除以销售额的比例为什么上升"):
            with patch.object(self.db, "execute_query", wraps=self.db.execute_query) as execute:
                result = self.ask(question)
            self.assertFalse(result["success"])
            self.assertIn("分子", result["error"])
            self.assertIsNone(result["attribution_data"])
            self.assertFalse(result["data"])
            execute.assert_not_called()

    def test_contribution_rate_is_output_not_an_underlying_rate_metric(self):
        result = self.ask("过去30天各品类退款额变化归因，给出各品类贡献率")
        self.assertTrue(result["success"], result["error"])
        analysis = result["attribution_data"]
        self.assertEqual(analysis["metric_name"], "total_refund_amount")
        self.assertAlmostEqual(sum(item["ratio"] for item in analysis["waterfall_items"]), 100)

    def test_regional_filter_defaults_to_category_breakdown(self):
        result = self.ask("为什么华东区退款额上升")
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(result["attribution_data"]["dimension"], "category_name")
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["details"]["sql"].count("region_name = '华东'"), 2)
        explicit = self.ask("华东退款额按地区归因")
        self.assertTrue(explicit["success"], explicit["error"])
        self.assertEqual(explicit["attribution_data"]["dimension"], "region_name")

    def test_invalid_dates_are_clarifications_on_every_fallback_path(self):
        for question in ("过去1000000000天退款额", "0001-01-01退款额", "2026-02-30退款金额"):
            result = self.ask(question)
            self.assertFalse(result["success"])
            self.assertTrue(result["clarification"]["need_clarification"])
        with patch.dict(os.environ, {"MOCK_LLM": "true"}), patch.object(ask_agent, "_call_llm", return_value="{}"):
            ask_agent.user_history_questions["demo-regression"] = []
            result = self.ask("2026-02-30退款金额")
        self.assertFalse(result["success"])
        self.assertTrue(result["clarification"]["need_clarification"])
        self.assertIn("日期无效", result["error"])

    def test_explicit_period_bounds_and_missing_period(self):
        current, baseline = question_periods("2026-08-01至2026-08-02退款额下降")
        self.assertEqual(current, {"start": "2026-08-01", "end": "2026-08-02"})
        self.assertEqual(baseline, {"start": "2026-07-30", "end": "2026-07-31"})
        result = self.ask("2000-01-01至2000-01-02退款额按品类下降")
        self.assertFalse(result["success"])
        self.assertIn("没有有效数据", result["error"])
        self.assertIsNone(result["attribution_data"])

    def test_calendar_ranges_reject_invalid_and_support_leap_year(self):
        current, baseline = question_periods("昨天退款同比归因", date(2024, 3, 1))
        self.assertEqual(current["start"], "2024-02-29")
        self.assertEqual(baseline["start"], "2023-02-28")
        for question in ("过去0天", "过去366天", "过去1000000000天", "2026-08-02至2026-08-01", "0001-01-01退款额", "2026-02-30退款额"):
            with self.assertRaises(ValueError):
                question_periods(question)


if __name__ == "__main__":
    unittest.main()
