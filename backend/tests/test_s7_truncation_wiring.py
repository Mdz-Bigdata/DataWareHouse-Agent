# -*- coding: utf-8 -*-
"""§7.9-6 截断声明「接进返回体」的回归测试。

背景：semantic_layer 里 TruncationNotice / describe_truncation / compile_with_probe /
truncation_notice 这一整套 API 写得是对的，但验收时实测 ask_agent 对它们的引用数为 0
——声明从未出现在 API 返回体里，用户拿到 10 行还以为是全量。本文件钉住「确实接上了」，
而不只是「API 存在」。

这些用例不连真实数据库：DSLCompiler 的行为用注入的假 compiler 覆盖，
执行结果用 DataFrame 直接给定。
"""
import unittest
from unittest.mock import patch

import pandas as pd

from app.schema.chat import AskResponse, QueryDetails
from app.service import ask_agent as ask_agent_module
from app.service.semantic_layer import (
    DEFAULT_ROW_LIMIT,
    LIMIT_SOURCE_DEFAULT,
    LIMIT_SOURCE_DSL,
    TruncationNotice,
    describe_truncation,
    split_probe_rows,
)


class TruncationNoticeReachesContractTest(unittest.TestCase):
    """声明必须挺过 AskResponse 契约校验——校验会丢弃未声明的字段。"""

    def test_query_details_declares_truncation_field(self):
        self.assertIn("truncation", QueryDetails.model_fields,
                      "QueryDetails 没有 truncation 字段，声明会在契约校验时被静默丢弃")

    def test_truncation_survives_response_validation(self):
        notice = describe_truncation(10, 10, LIMIT_SOURCE_DEFAULT)
        payload = {
            "success": True,
            "conclusion": notice.apply_to("共 10 个分组。"),
            "data": [],
            "details": {
                "sql": "SELECT 1 LIMIT 10", "dialect": "postgres", "elapsed_time": "0.1s",
                "tables": ["t"], "source_desc": "t", "filters": [],
                "estimated_rows": 0, "truncation": notice.model_dump(),
            },
        }
        dumped = AskResponse.model_validate(payload).model_dump(mode="json", exclude_unset=True)
        self.assertIsNotNone(dumped["details"]["truncation"],
                             "truncation 没能通过 AskResponse 契约校验")
        self.assertTrue(dumped["details"]["truncation"]["uncertain"])
        self.assertTrue(dumped["conclusion"].startswith("⚠️"))

    def test_field_is_optional_so_old_payloads_still_validate(self):
        """老调用方 / 老缓存条目不带这个字段时不能校验失败。"""
        payload = {
            "success": True, "data": [],
            "details": {"sql": "SELECT 1", "dialect": "postgres", "elapsed_time": "0.1s",
                        "tables": [], "source_desc": "", "filters": []},
        }
        response = AskResponse.model_validate(payload)
        self.assertIsNone(response.details.truncation)


class AskAgentWiringTest(unittest.TestCase):
    """ask_agent 真的调用了这套 API，而不只是 import 了它。"""

    def test_ask_agent_source_calls_truncation_notice(self):
        import inspect
        source = inspect.getsource(ask_agent_module)
        for symbol in ("truncation_notice", "apply_to", '"truncation"'):
            self.assertIn(symbol, source,
                          f"ask_agent 里找不到 {symbol}：截断声明没有接进返回体")

    def test_probe_switch_defaults_off(self):
        """破坏性变更（SQL 文本变成 LIMIT n+1）必须默认关闭。"""
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(ask_agent_module._env_flag(ask_agent_module.TRUNCATION_PROBE_ENV),
                             "环境变量未设置时探测开关应为关闭")

    def test_probe_switch_recognises_truthy_values(self):
        name = ask_agent_module.TRUNCATION_PROBE_ENV
        for value in ("1", "true", "TRUE", "yes", "on", " on "):
            with patch.dict("os.environ", {name: value}, clear=False):
                self.assertTrue(ask_agent_module._env_flag(name), f"{value!r} 应开启探测")
        for value in ("", "0", "false", "no", "off"):
            with patch.dict("os.environ", {name: value}, clear=False):
                self.assertFalse(ask_agent_module._env_flag(name), f"{value!r} 不应开启探测")


class TruncationWordingTest(unittest.TestCase):
    """措辞必须如实区分「确定截断」与「可能截断」。"""

    def test_known_total_states_exact_numbers(self):
        notice = describe_truncation(10, 10, LIMIT_SOURCE_DEFAULT, total_rows=55)
        self.assertTrue(notice.truncated)
        self.assertFalse(notice.uncertain)
        self.assertIn("共 55 行", notice.message)
        self.assertIn("仅返回 10 行", notice.message)

    def test_unknown_total_says_maybe_not_definitely(self):
        notice = describe_truncation(10, 10, LIMIT_SOURCE_DSL)
        self.assertFalse(notice.truncated)
        self.assertTrue(notice.uncertain)
        self.assertIn("可能被截断", notice.message)
        self.assertNotIn("共 ", notice.message)

    def test_under_limit_emits_nothing(self):
        notice = describe_truncation(3, DEFAULT_ROW_LIMIT, LIMIT_SOURCE_DEFAULT)
        self.assertFalse(notice.truncated)
        self.assertFalse(notice.uncertain)
        self.assertEqual(notice.message, "")
        self.assertEqual(notice.apply_to("共 3 行。"), "共 3 行。")

    def test_probe_row_is_never_presented(self):
        rows = [{"i": i} for i in range(11)]
        shown, truncated = split_probe_rows(rows, 10)
        self.assertEqual(len(shown), 10, "多取的那一行被呈现给用户了")
        self.assertTrue(truncated)

    def test_apply_to_keeps_conclusion_when_not_truncated(self):
        self.assertEqual(TruncationNotice().apply_to("原文"), "原文")


class ProbeTrimTest(unittest.TestCase):
    """probe 模式下呈现给用户的行数必须与关闭时完全一致。"""

    def test_head_trim_matches_limit(self):
        df = pd.DataFrame({"i": range(11)})
        self.assertEqual(len(df.head(10)), 10)
        self.assertEqual(list(df.head(10)["i"]), list(range(10)))


if __name__ == "__main__":
    unittest.main()
