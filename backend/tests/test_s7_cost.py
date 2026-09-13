# -*- coding: utf-8 -*-
"""§7.9-8 LLM token 成本日表 + 预算告警线 的回归测试。

在此之前 ``backend/app`` 全库没有任何一处 token/成本记账，模型路由只 ``print``
一行就把用量丢了——「这个月花了多少钱」根本答不出来。这组测试钉死四件事：

1. **单价与折算**：prompt / completion 分别计价；单价表里没有的模型**不能按 0 元
   静默记账**，必须退到档位兜底价并标记出来。
2. **聚合口径**：按 日期 × 模型档位 × 模型 × 厂商 × 数据源 聚合。本系统支持 7 种引擎，
   换数据源必须落到不同的行，否则「哪个源最烧钱」无从回答。
3. **两条告警线**（外部给定，逐字核对）：环比增长 > 10%、预算水位 > 80%。
   边界是**严格大于**：正好 10% / 正好 80% 不告警。
4. **接入安全**：计量挂在 ``ask_agent._call_llm`` 外层，计量失败绝不能影响问数主流程。
"""
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("DB_TYPE", "sqlite")

from app.service.llm_cost import (
    ALERT_BUDGET_WATERLINE,
    ALERT_RING_GROWTH_RATIO,
    DEFAULT_PRICE_BOOK,
    LlmCostLedger,
    Price,
    estimate_tokens,
    record_llm_usage,
    resolve_price,
)

TODAY = date(2026, 9, 13)
YESTERDAY = TODAY - timedelta(days=1)


def new_ledger(**kwargs) -> LlmCostLedger:
    """每个用例一本干净的账本，并固定成内置示意单价，不受环境变量影响。"""
    kwargs.setdefault("price_book", DEFAULT_PRICE_BOOK)
    kwargs.setdefault("persist_path", None)
    return LlmCostLedger(**kwargs)


class PriceBookTest(unittest.TestCase):
    """单价解析：更具体的型号优先，未知模型不得按 0 元入账。"""

    def test_specific_model_wins_over_generic_prefix(self):
        # "gpt-4o-mini" 不能被 "gpt-4o" 抢走，否则便宜档会按贵档记账，成本凭空翻十几倍。
        mini = resolve_price("gpt-4o-mini", "fast")
        full = resolve_price("gpt-4o", "complex")
        self.assertLess(mini.prompt_per_1k, full.prompt_per_1k)
        self.assertEqual(mini.source, "price_book")

    def test_unknown_model_falls_back_to_tier_price_not_zero(self):
        price = resolve_price("some-brand-new-llm-v9", "complex")
        self.assertEqual(price.source, "tier_default")
        # 关键：未知模型按 0 元记账 = 凭空低估花销，这是本模块最不能犯的错。
        self.assertGreater(price.prompt_per_1k, 0)
        self.assertGreater(price.completion_per_1k, 0)

    def test_fast_tier_fallback_is_cheaper_than_complex(self):
        self.assertLess(
            resolve_price("unknown-x", "fast").prompt_per_1k,
            resolve_price("unknown-x", "complex").prompt_per_1k,
        )

    def test_mock_vendor_is_always_free(self):
        price = resolve_price("gpt-4o", "complex", vendor="mock")
        self.assertEqual(price.cost_yuan(10_000, 10_000), 0.0)
        self.assertEqual(price.source, "mock")

    def test_prompt_and_completion_priced_separately(self):
        price = Price(prompt_per_1k=0.01, completion_per_1k=0.04)
        # 1000 prompt + 500 completion = 0.01 + 0.02
        self.assertAlmostEqual(price.cost_yuan(1000, 500), 0.03, places=9)

    def test_negative_tokens_never_produce_negative_cost(self):
        price = Price(prompt_per_1k=0.01, completion_per_1k=0.04)
        self.assertEqual(price.cost_yuan(-100, -100), 0.0)

    def test_set_price_overrides_builtin(self):
        ledger = new_ledger()
        ledger.set_price("gpt-4o", {"prompt_per_1k": 1.0, "completion_per_1k": 2.0, "source": "override"})
        price = ledger.price_for("gpt-4o", "complex")
        self.assertEqual((price.prompt_per_1k, price.completion_per_1k), (1.0, 2.0))

    def test_set_price_rejects_garbage(self):
        with self.assertRaises(ValueError):
            new_ledger().set_price("gpt-4o", "not-a-price")


class TokenEstimationTest(unittest.TestCase):
    """厂商不回 usage 时的兜底估算：只求量级对，但必须被标记成「估的」。"""

    def test_chinese_costs_more_tokens_per_char_than_ascii(self):
        chinese = estimate_tokens("华东区昨天销售额排名前三的品类分别是什么")
        ascii_same_len = estimate_tokens("a" * 20)
        self.assertGreater(chinese, ascii_same_len)

    def test_empty_and_non_string_are_zero(self):
        for value in ("", None, 123, []):
            self.assertEqual(estimate_tokens(value), 0)

    def test_estimated_calls_are_flagged(self):
        ledger = new_ledger()
        ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                      data_source="pg", prompt_text="你好" * 100, completion_text="ok", day=TODAY)
        row = ledger.rows(day=TODAY)[0]
        self.assertEqual(row["estimated_calls"], 1)
        self.assertGreater(row["prompt_tokens"], 0)

    def test_reported_usage_is_not_flagged_as_estimated(self):
        ledger = new_ledger()
        ledger.record(model="gpt-4o", model_tier="complex", vendor="openai", data_source="pg",
                      prompt_tokens=1200, completion_tokens=300, day=TODAY)
        row = ledger.rows(day=TODAY)[0]
        self.assertEqual(row["estimated_calls"], 0)
        self.assertEqual(row["prompt_tokens"], 1200)


class DailyTableAggregationTest(unittest.TestCase):
    """成本日表的聚合口径：日期 × 档位 × 模型 × 厂商 × 数据源。"""

    def setUp(self):
        self.ledger = new_ledger()

    def test_same_key_accumulates_into_one_row(self):
        for _ in range(3):
            self.ledger.record(model="gpt-4o-mini", model_tier="fast", vendor="openai",
                               data_source="pg-blog", prompt_tokens=1000,
                               completion_tokens=200, day=TODAY)
        rows = self.ledger.rows(day=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calls"], 3)
        self.assertEqual(rows[0]["prompt_tokens"], 3000)
        self.assertEqual(rows[0]["total_tokens"], 3600)

    def test_different_data_source_splits_rows(self):
        # 本系统支持 7 种引擎；不按数据源拆就答不出「哪个源最烧钱」。
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg-blog", prompt_tokens=1000, completion_tokens=0, day=TODAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="doris-dw", prompt_tokens=4000, completion_tokens=0, day=TODAY)
        rows = self.ledger.rows(day=TODAY)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            self.ledger.cost_on(day=TODAY, data_source="doris-dw"),
            4 * self.ledger.cost_on(day=TODAY, data_source="pg-blog"),
        )

    def test_different_tier_splits_rows(self):
        self.ledger.record(model="gpt-4o", model_tier="fast", vendor="openai",
                           data_source="pg", prompt_tokens=100, completion_tokens=0, day=TODAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg", prompt_tokens=100, completion_tokens=0, day=TODAY)
        self.assertEqual(len(self.ledger.rows(day=TODAY)), 2)

    def test_different_day_splits_rows(self):
        self.ledger.record(model="gpt-4o", vendor="openai", data_source="pg",
                           prompt_tokens=100, completion_tokens=0, day=TODAY)
        self.ledger.record(model="gpt-4o", vendor="openai", data_source="pg",
                           prompt_tokens=100, completion_tokens=0, day=YESTERDAY)
        self.assertEqual(self.ledger.days(), [YESTERDAY.isoformat(), TODAY.isoformat()])

    def test_failed_call_is_still_billed(self):
        # 厂商对失败请求通常照收 prompt token 的钱；不记就是漏账。
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai", data_source="pg",
                           prompt_tokens=500, completion_tokens=0, ok=False, day=TODAY)
        row = self.ledger.rows(day=TODAY)[0]
        self.assertEqual(row["calls"], 1)
        self.assertEqual(row["failed_calls"], 1)
        self.assertGreater(row["cost_yuan"], 0)

    def test_cost_matches_price_book_math(self):
        price = self.ledger.price_for("deepseek-chat", "fast")
        self.ledger.record(model="deepseek-chat", model_tier="fast", vendor="deepseek",
                           data_source="pg", prompt_tokens=2000, completion_tokens=1000, day=TODAY)
        expected = 2 * price.prompt_per_1k + 1 * price.completion_per_1k
        self.assertAlmostEqual(self.ledger.cost_on(day=TODAY), round(expected, 6), places=6)

    def test_garbage_input_never_raises_and_never_corrupts(self):
        row = self.ledger.record(model=None, model_tier=None, vendor=None, data_source=None,
                                 prompt_tokens="not-a-number", completion_tokens=-99,
                                 day="not-a-date")
        self.assertEqual(row.prompt_tokens, 0)
        self.assertEqual(row.completion_tokens, 0)
        self.assertEqual(row.model, "unknown")
        self.assertEqual(row.day, date.today().isoformat())

    def test_ledger_is_bounded_by_max_days(self):
        ledger = new_ledger(max_days=3)
        for offset in range(10):
            ledger.record(model="gpt-4o", vendor="openai", data_source="pg",
                          prompt_tokens=10, completion_tokens=0,
                          day=TODAY - timedelta(days=offset))
        self.assertEqual(len(ledger.days()), 3)
        # 留下的必须是最近三天，不能把新数据淘汰掉。
        self.assertEqual(ledger.days()[-1], TODAY.isoformat())


class QueryInterfaceTest(unittest.TestCase):
    """「某天 / 某模型花了多少」的查询接口。"""

    def setUp(self):
        self.ledger = new_ledger()
        self.ledger.record(model="gpt-4o-mini", model_tier="fast", vendor="openai",
                           data_source="pg", prompt_tokens=10_000, completion_tokens=2_000, day=TODAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg", prompt_tokens=10_000, completion_tokens=2_000, day=TODAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg", prompt_tokens=5_000, completion_tokens=1_000, day=YESTERDAY)

    def test_cost_for_one_day(self):
        today_cost = self.ledger.cost_on(day=TODAY)
        yesterday_cost = self.ledger.cost_on(day=YESTERDAY)
        self.assertGreater(today_cost, yesterday_cost)
        self.assertGreater(yesterday_cost, 0)

    def test_cost_for_one_model_on_one_day(self):
        mini = self.ledger.cost_on(day=TODAY, model="gpt-4o-mini")
        total = self.ledger.cost_on(day=TODAY)
        self.assertGreater(mini, 0)
        self.assertLess(mini, total)
        # complex 档的 gpt-4o 同样 token 数必须显著贵于 mini——档位选型的成本依据。
        self.assertGreater(self.ledger.cost_on(day=TODAY, model_tier="complex"), mini * 5)

    def test_model_filter_is_substring_and_case_insensitive(self):
        self.assertEqual(len(self.ledger.rows(day=TODAY, model="GPT-4O")), 2)
        self.assertEqual(len(self.ledger.rows(day=TODAY, model="gpt-4o-mini")), 1)

    def test_cost_on_without_day_is_all_time_total(self):
        self.assertAlmostEqual(
            self.ledger.cost_on(),
            round(self.ledger.cost_on(day=TODAY) + self.ledger.cost_on(day=YESTERDAY), 6),
            places=6,
        )

    def test_summary_reports_tokens_and_calls(self):
        summary = self.ledger.summary(day=TODAY)
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["prompt_tokens"], 20_000)
        self.assertEqual(summary["total_tokens"], 24_000)

    def test_daily_totals_are_ascending_and_limitable(self):
        totals = self.ledger.daily_totals()
        self.assertEqual([item["day"] for item in totals],
                         [YESTERDAY.isoformat(), TODAY.isoformat()])
        self.assertEqual(len(self.ledger.daily_totals(limit=1)), 1)

    def test_month_total_sums_the_calendar_month(self):
        month = self.ledger.month_total(TODAY.strftime("%Y-%m"))
        self.assertAlmostEqual(month, self.ledger.cost_on(), places=6)

    def test_unknown_filter_matches_nothing(self):
        self.assertEqual(self.ledger.rows(day=TODAY, data_source="no-such-source"), [])
        self.assertEqual(self.ledger.cost_on(day=TODAY, data_source="no-such-source"), 0.0)


class AlertLineTest(unittest.TestCase):
    """两条告警线：环比增长 > 10%、预算水位 > 80%。阈值与边界都逐字核对。"""

    def setUp(self):
        self.ledger = new_ledger()

    def _spend(self, day, yuan_tokens):
        """按 token 数投喂花销（单价固定，token 数成比例即成本成比例）。"""
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg", prompt_tokens=yuan_tokens,
                           completion_tokens=0, day=day)

    def test_threshold_constants_are_exactly_the_given_numbers(self):
        self.assertEqual(ALERT_RING_GROWTH_RATIO, 0.10)
        self.assertEqual(ALERT_BUDGET_WATERLINE, 0.80)

    def test_ring_growth_above_ten_percent_alerts(self):
        self._spend(YESTERDAY, 10_000)
        self._spend(TODAY, 12_000)  # +20%
        alerts = self.ledger.budget_alerts(day=TODAY)
        self.assertEqual([a["code"] for a in alerts], ["cost_ring_growth"])
        self.assertAlmostEqual(alerts[0]["growth_ratio"], 0.2, places=6)

    def test_ring_growth_exactly_ten_percent_does_not_alert(self):
        # 告警线是「> 10%」，正好 10% 不告警。边界写错会天天误报。
        self._spend(YESTERDAY, 10_000)
        self._spend(TODAY, 11_000)
        growth = self.ledger.ring_growth(day=TODAY)
        self.assertAlmostEqual(growth["growth_ratio"], 0.10, places=9)
        self.assertFalse(growth["breached"])
        self.assertEqual(self.ledger.budget_alerts(day=TODAY), [])

    def test_ring_growth_decline_does_not_alert(self):
        self._spend(YESTERDAY, 10_000)
        self._spend(TODAY, 5_000)
        self.assertFalse(self.ledger.ring_growth(day=TODAY)["breached"])

    def test_zero_base_reports_none_ratio_and_no_alert(self):
        # 昨天没花钱、今天开始花，不是「成本失控」，不该拿 inf 去吓人。
        self._spend(TODAY, 10_000)
        growth = self.ledger.ring_growth(day=TODAY)
        self.assertIsNone(growth["growth_ratio"])
        self.assertFalse(growth["breached"])

    def test_ring_growth_can_be_scoped_to_one_model_or_source(self):
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg", prompt_tokens=1_000, completion_tokens=0, day=YESTERDAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="pg", prompt_tokens=5_000, completion_tokens=0, day=TODAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="doris", prompt_tokens=9_000, completion_tokens=0, day=YESTERDAY)
        self.ledger.record(model="gpt-4o", model_tier="complex", vendor="openai",
                           data_source="doris", prompt_tokens=9_000, completion_tokens=0, day=TODAY)
        self.assertTrue(self.ledger.ring_growth(day=TODAY, data_source="pg")["breached"])
        self.assertFalse(self.ledger.ring_growth(day=TODAY, data_source="doris")["breached"])

    def test_budget_waterline_above_eighty_percent_alerts(self):
        self._spend(TODAY, 100_000)
        used = self.ledger.cost_on(day=TODAY)
        budget = used / 0.9  # 水位 90%
        alerts = self.ledger.budget_alerts(day=TODAY, daily_budget_yuan=budget)
        codes = [a["code"] for a in alerts]
        self.assertIn("budget_waterline", codes)
        alert = next(a for a in alerts if a["code"] == "budget_waterline")
        self.assertAlmostEqual(alert["ratio"], 0.9, places=4)
        self.assertEqual(alert["threshold"], 0.80)

    def test_budget_waterline_exactly_eighty_percent_does_not_alert(self):
        self._spend(TODAY, 100_000)
        used = self.ledger.cost_on(day=TODAY)
        waterline = self.ledger.budget_waterline(used / 0.8, day=TODAY, scope="day")
        self.assertAlmostEqual(waterline["ratio"], 0.80, places=6)
        self.assertFalse(waterline["breached"])

    def test_monthly_budget_uses_month_to_date_spend(self):
        self._spend(YESTERDAY, 60_000)
        self._spend(TODAY, 60_000)
        month_used = self.ledger.month_total(TODAY.strftime("%Y-%m"))
        day_used = self.ledger.cost_on(day=TODAY)
        self.assertGreater(month_used, day_used)
        # 按当月预算算水位：当天没超、当月超了，必须以当月为准报出来。
        budget = month_used / 0.85
        alerts = self.ledger.budget_alerts(day=TODAY, monthly_budget_yuan=budget)
        self.assertIn("budget_waterline", [a["code"] for a in alerts])
        self.assertEqual(
            next(a for a in alerts if a["code"] == "budget_waterline")["scope"], "month"
        )

    def test_non_positive_budget_raises_instead_of_silently_passing(self):
        self._spend(TODAY, 1_000)
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                self.ledger.budget_waterline(bad, day=TODAY)

    def test_invalid_scope_raises(self):
        with self.assertRaises(ValueError):
            self.ledger.budget_waterline(100.0, day=TODAY, scope="week")

    def test_both_alerts_can_fire_together(self):
        self._spend(YESTERDAY, 10_000)
        self._spend(TODAY, 50_000)
        used = self.ledger.cost_on(day=TODAY)
        alerts = self.ledger.budget_alerts(day=TODAY, daily_budget_yuan=used / 0.95)
        self.assertEqual({a["code"] for a in alerts}, {"cost_ring_growth", "budget_waterline"})
        for alert in alerts:
            self.assertTrue(alert["message"])
            self.assertEqual(alert["level"], "warn")

    def test_no_alert_when_everything_is_within_lines(self):
        self._spend(YESTERDAY, 10_000)
        self._spend(TODAY, 10_500)  # +5%
        used = self.ledger.cost_on(day=TODAY)
        self.assertEqual(self.ledger.budget_alerts(day=TODAY, daily_budget_yuan=used / 0.5), [])


class PersistenceTest(unittest.TestCase):
    """可选的 JSONL 事件流：默认不落盘，开了能重放重建。"""

    def test_no_file_written_by_default(self):
        ledger = new_ledger()
        ledger.record(model="gpt-4o", vendor="openai", data_source="pg",
                      prompt_tokens=10, completion_tokens=0, day=TODAY)
        self.assertIsNone(ledger._persist_path)

    def test_replay_rebuilds_the_same_totals(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cost.jsonl")
            source = new_ledger(persist_path=path)
            source.record(model="gpt-4o", model_tier="complex", vendor="openai",
                          data_source="pg", prompt_tokens=1000, completion_tokens=500, day=TODAY)
            source.record(model="gpt-4o-mini", model_tier="fast", vendor="openai",
                          data_source="doris", prompt_tokens=800, completion_tokens=100, day=YESTERDAY)
            restored = new_ledger()
            self.assertEqual(restored.replay_events(path), 2)
            self.assertAlmostEqual(restored.cost_on(day=TODAY), source.cost_on(day=TODAY), places=9)
            self.assertEqual(restored.rows(day=YESTERDAY)[0]["data_source"], "doris")

    def test_corrupt_lines_are_skipped_not_fatal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cost.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json}\n")
                handle.write('{"day": "2026-09-13", "model": "gpt-4o", "model_tier": "complex",'
                             ' "vendor": "openai", "data_source": "pg", "prompt_tokens": 100,'
                             ' "completion_tokens": 10}\n')
                handle.write("\n")
            ledger = new_ledger()
            self.assertEqual(ledger.replay_events(path), 1)

    def test_replay_does_not_re_append_to_its_own_event_stream(self):
        """重放不能把事件再写回同一个文件——否则每重启一次文件翻一倍、重复记账。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cost.jsonl")
            writer = new_ledger(persist_path=path)
            for _ in range(3):
                writer.record(model="gpt-4o", model_tier="complex", vendor="openai",
                              data_source="pg", prompt_tokens=100, completion_tokens=10, day=TODAY)
            with open(path, encoding="utf-8") as handle:
                lines_before = len(handle.readlines())

            # 进程重启：同一个 persist_path 的新账本从文件重建自己。
            restarted = new_ledger(persist_path=path)
            self.assertEqual(restarted.replay_events(), 3)
            with open(path, encoding="utf-8") as handle:
                lines_after = len(handle.readlines())
            self.assertEqual(lines_before, lines_after)
            self.assertAlmostEqual(restarted.cost_on(day=TODAY), writer.cost_on(day=TODAY), places=9)

    def test_replay_missing_file_is_zero_not_error(self):
        self.assertEqual(new_ledger().replay_events("/nonexistent/path/cost.jsonl"), 0)


class PriceSourceHonestyTest(unittest.TestCase):
    """成本行必须如实说明自己是按真实单价算的还是按兜底价估的。"""

    def test_row_priced_from_book_is_marked_price_book(self):
        ledger = new_ledger()
        ledger.record(model="gpt-4o", model_tier="complex", vendor="openai", data_source="pg",
                      prompt_tokens=100, completion_tokens=0, day=TODAY)
        self.assertEqual(ledger.rows(day=TODAY)[0]["price_source"], "price_book")

    def test_unknown_model_row_is_marked_tier_default(self):
        ledger = new_ledger()
        ledger.record(model="brand-new-model-x", model_tier="complex", vendor="openai",
                      data_source="pg", prompt_tokens=100, completion_tokens=0, day=TODAY)
        row = ledger.rows(day=TODAY)[0]
        self.assertEqual(row["price_source"], "tier_default")
        self.assertGreater(row["cost_yuan"], 0)  # 兜底价也是钱，不能记成 0

    def test_uncertainty_is_sticky_within_a_row(self):
        # 同一行里先按兜底价记了一笔，之后再补真实单价，整行也不能自称精确。
        ledger = new_ledger()
        ledger.record(model="mystery", model_tier="fast", vendor="openai", data_source="pg",
                      prompt_tokens=100, completion_tokens=0, day=TODAY)
        ledger.set_price("mystery", {"prompt_per_1k": 0.5, "completion_per_1k": 0.5})
        ledger.record(model="mystery", model_tier="fast", vendor="openai", data_source="pg",
                      prompt_tokens=100, completion_tokens=0, day=TODAY)
        self.assertEqual(ledger.rows(day=TODAY)[0]["price_source"], "tier_default")


class RecordLlmUsageSafetyTest(unittest.TestCase):
    """记账入口永不抛异常——成本表不该有能力弄挂问数。"""

    def test_never_raises_even_if_ledger_explodes(self):
        with patch("app.service.llm_cost.llm_cost_ledger.record",
                   side_effect=RuntimeError("ledger on fire")):
            self.assertIsNone(record_llm_usage(model="gpt-4o", model_tier="fast"))

    def test_never_raises_on_unexpected_kwargs(self):
        self.assertIsNone(record_llm_usage(model="gpt-4o", nonsense_kwarg=1))


class AskAgentMeteringTest(unittest.TestCase):
    """接入点：``ask_agent._call_llm`` 外层记账，且计量不得干扰调用行为。"""

    def setUp(self):
        from app.service.ask_agent import ask_agent

        self.agent = ask_agent
        self.recorded = []
        self.patcher = patch("app.service.ask_agent.record_llm_usage",
                             side_effect=lambda **kw: self.recorded.append(kw))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    @staticmethod
    def _raw(vendor="openai", model="gpt-4o", usage=None, text="ok", error=None):
        """假的 _call_llm_raw：像真实现那样填 _usage_sink，然后返回或抛。"""

        def fake(prompt, system_prompt="", user="anonymous", model_tier="fast", _usage_sink=None):
            if _usage_sink is not None:
                _usage_sink["vendor"] = vendor
                _usage_sink["model"] = model
                if usage:
                    _usage_sink.update(usage)
            if error is not None:
                raise error
            return text

        return fake

    def test_successful_call_is_metered_with_reported_usage(self):
        with patch.object(self.agent, "_call_llm_raw",
                          side_effect=self._raw(usage={"prompt_tokens": 1234, "completion_tokens": 56})):
            result = self.agent._call_llm("问题", system_prompt="sys", model_tier="complex")
        self.assertEqual(result, "ok")  # 返回值原样透传，计量不改调用结果
        self.assertEqual(len(self.recorded), 1)
        entry = self.recorded[0]
        self.assertEqual(entry["model"], "gpt-4o")
        self.assertEqual(entry["model_tier"], "complex")
        self.assertEqual(entry["vendor"], "openai")
        self.assertEqual(entry["prompt_tokens"], 1234)
        self.assertEqual(entry["completion_tokens"], 56)
        self.assertTrue(entry["ok"])

    def test_missing_vendor_usage_falls_back_to_text_estimation(self):
        with patch.object(self.agent, "_call_llm_raw", side_effect=self._raw(usage=None)):
            self.agent._call_llm("华东区昨天的退款额是多少", system_prompt="sys")
        entry = self.recorded[0]
        self.assertIsNone(entry["prompt_tokens"])       # 交给账本估算
        self.assertIn("华东区", entry["prompt_text"])    # 估算原文带上了 prompt

    def test_failed_call_is_metered_and_exception_still_propagates(self):
        boom = RuntimeError("vendor 503")
        with patch.object(self.agent, "_call_llm_raw", side_effect=self._raw(error=boom)):
            with self.assertRaises(RuntimeError):
                self.agent._call_llm("问题")
        self.assertEqual(len(self.recorded), 1)
        self.assertFalse(self.recorded[0]["ok"])

    def test_call_that_never_reached_a_vendor_is_not_billed(self):
        # API Key 未配置之类：请求压根没发出去，不能记一笔不存在的花销。
        def never_reached(prompt, system_prompt="", user="anonymous", model_tier="fast", _usage_sink=None):
            raise RuntimeError("系统大模型 API Key 未配置")

        with patch.dict(os.environ, {"MOCK_LLM": "false"}):
            with patch.object(self.agent, "_call_llm_raw", side_effect=never_reached):
                with self.assertRaises(RuntimeError):
                    self.agent._call_llm("问题")
        self.assertEqual(self.recorded, [])

    def test_mock_llm_is_metered_as_free(self):
        def mock_raw(prompt, system_prompt="", user="anonymous", model_tier="fast", _usage_sink=None):
            return "mocked"

        with patch.dict(os.environ, {"MOCK_LLM": "true"}):
            with patch.object(self.agent, "_call_llm_raw", side_effect=mock_raw):
                self.agent._call_llm("问题")
        self.assertEqual(self.recorded[0]["vendor"], "mock")
        # 本地假调用不能产生真实花销。
        from app.service.llm_cost import resolve_price
        self.assertEqual(resolve_price("mock", "fast", vendor="mock").cost_yuan(999, 999), 0.0)

    def test_metering_failure_never_breaks_the_query(self):
        self.patcher.stop()
        with patch("app.service.ask_agent.record_llm_usage",
                   side_effect=RuntimeError("metering exploded")):
            with patch.object(self.agent, "_call_llm_raw", side_effect=self._raw(text="answer")):
                result = self.agent._call_llm("问题")
        self.assertEqual(result, "answer")  # 问数照常返回
        self.patcher.start()

    def test_extract_usage_reads_openai_shape(self):
        class Usage:
            prompt_tokens, completion_tokens = 11, 22

        class Response:
            usage = Usage()

        sink = {}
        self.agent._extract_usage(sink, Response())
        self.assertEqual((sink["prompt_tokens"], sink["completion_tokens"]), (11, 22))

    def test_extract_usage_reads_gemini_shape(self):
        class Meta:
            prompt_token_count, candidates_token_count = 33, 44

        class Response:
            usage = None
            usage_metadata = Meta()

        sink = {}
        self.agent._extract_usage(sink, Response())
        self.assertEqual((sink["prompt_tokens"], sink["completion_tokens"]), (33, 44))

    def test_extract_usage_tolerates_response_without_usage(self):
        sink = {}
        self.agent._extract_usage(sink, object())
        self.assertEqual(sink, {})  # 留空 -> 由估算兜底，不炸


class LiveLedgerIntegrationTest(unittest.TestCase):
    """走真实的全局账本跑一遍接入点，确认端到端能答出「今天花了多少」。"""

    def test_call_llm_lands_in_the_global_ledger(self):
        from app.service.ask_agent import ask_agent
        from app.service.llm_cost import llm_cost_ledger

        llm_cost_ledger.clear()

        def fake_raw(prompt, system_prompt="", user="anonymous", model_tier="fast", _usage_sink=None):
            _usage_sink["vendor"] = "openai"
            _usage_sink["model"] = "gpt-4o"
            _usage_sink["prompt_tokens"] = 10_000
            _usage_sink["completion_tokens"] = 2_000
            return "ok"

        try:
            with patch.object(ask_agent, "_call_llm_raw", side_effect=fake_raw):
                ask_agent._call_llm("昨天总交易额是多少", model_tier="complex")
            today = date.today()
            self.assertGreater(llm_cost_ledger.cost_on(day=today, model="gpt-4o"), 0)
            row = llm_cost_ledger.rows(day=today)[0]
            self.assertEqual(row["model_tier"], "complex")
            self.assertEqual(row["total_tokens"], 12_000)
            # 数据源维度必须被填上（取不到也得是 unknown，不能是空）。
            self.assertTrue(row["data_source"])
        finally:
            llm_cost_ledger.clear()


if __name__ == "__main__":
    unittest.main()
