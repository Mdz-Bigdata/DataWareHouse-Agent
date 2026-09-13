# -*- coding: utf-8 -*-
"""
验收审计回归测试（独立于八个改动 agent 自己的测试）。

这个文件只钉住「我亲手实测过、且上游描述与事实有出入」的那几条，
不重复各组自己的用例：

  1. metadata_enricher 旁路取数通道：恶意表名必须在「任何 SQL 落库之前」被拒；
     这里用间谍替换 db_service.execute_query，断言零 SQL 落到数据库层。
  2. guardrail.large_tables 必须真的有写入点，且分区裁剪闸门能被 check_sql 触发
     （历史上它是死闸门：全仓零写入点，large_tables 永远为空）。
  3. 向量点 ID 必须是内容派生的确定性 UUID，重复 ingest 不产生新点。
  4. ask() 的 trace_id 形参必须存在且可选（既有位置参数调用方不能被破坏）。
  5. 落盘的用户记忆里不允许再出现「未标注的伪造演示历史」——
     代码里的 seed 已被删除，但历史遗留的两条已经写进了生产数据文件。
"""
import inspect
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.service import db_service as db_service_module


BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 被删掉的硬编码 seed 里那两个编造出来的结论文案。
FABRICATED_SUMMARIES = (
    "过去30天华东区GMV为 ¥1,234.50 万",
    "近6月GMV呈稳步上升趋势，在 5 月达到峰值 ¥1,235 万",
)
DEMO_MARKERS = ("【示例", "示例数据")


class BypassChannelClosedTest(unittest.TestCase):
    """★ 旁路取数通道：拒绝必须发生在取数之前，而不是靠数据库自己报错。"""

    INJECTIONS = [
        "x; DROP TABLE y",
        "articles; DROP TABLE articles",
        "x UNION SELECT 1,2,3",
        "articles UNION SELECT password FROM users",
        "articles--",
        "articles/*c*/",
        "(SELECT 1)",
        "'articles'",
        '"articles"',
        "`articles`",
        "articles WHERE 1=1",
        "articles\n; DELETE FROM articles",
        "../../etc/passwd",
        "1articles",
        "",
        "   ",
        None,
        123,
        ["articles"],
        "a" * 300,
    ]

    # 形状合法但未注册 / 越权的表名，同样必须在白名单关卡被拒。
    UNREGISTERED = [
        "nonexistent_table_zzz",
        "information_schema.tables",
        "pg_catalog.pg_user",
        "db.public.articles",
        "sqlite_master",
    ]

    def setUp(self):
        from app.service.metadata_enricher import MetadataEnricher

        self.enricher = MetadataEnricher()
        # 白名单固定成一份服务端清单，用例不依赖（也不打扰）线上数据源，
        # 但 resolve_registered_table 的三道关卡仍然全程真实执行。
        self.enricher.registered_relations = lambda: ["articles", "dim_region"]
        schema_patch = patch.object(
            db_service_module.db_service, "query_schemas", ["public"], create=True)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)
        self.executed = []

        original = db_service_module.db_service.execute_query

        def spy(sql, *args, **kwargs):
            self.executed.append(sql)
            raise AssertionError(
                f"安全回归: 非法表名不应产生任何落库 SQL，但收到了: {sql!r}")

        db_service_module.db_service.execute_query = spy
        self.addCleanup(
            setattr, db_service_module.db_service, "execute_query", original)

    def test_injection_payloads_are_rejected_before_any_sql_runs(self):
        from app.service.metadata_enricher import UnsafeTableNameError

        for payload in self.INJECTIONS:
            with self.subTest(payload=payload):
                with self.assertRaises(UnsafeTableNameError):
                    self.enricher.profile_table(payload)
        self.assertEqual(
            self.executed, [], "注入载荷不允许有任何 SQL 落到数据库层")

    def test_unregistered_tables_are_rejected_before_any_sql_runs(self):
        from app.service.metadata_enricher import UnsafeTableNameError

        for name in self.UNREGISTERED:
            with self.subTest(name=name):
                with self.assertRaises(UnsafeTableNameError):
                    self.enricher.profile_table(name)
        self.assertEqual(self.executed, [])

    def test_sample_size_cannot_carry_a_payload(self):
        from app.service.metadata_enricher import UnsafeSampleSizeError

        for bad in ["5; DROP TABLE articles", "-1", "0", "abc", None, 0]:
            with self.subTest(sample_size=bad):
                with self.assertRaises(UnsafeSampleSizeError):
                    self.enricher._normalize_sample_size(bad)

    def test_generated_sql_never_interpolates_caller_text(self):
        """SQL 由 AST 构造，标识符带引号，LIMIT 是整数。"""
        sql = self.enricher.build_sampling_sql(None, "articles", 10)
        self.assertIn("articles", sql)
        self.assertIn("10", sql)
        self.assertNotIn(";", sql)
        # 上限保护：超大 sample_size 会被压到 MAX_SAMPLE_SIZE，而不是原样拼进去。
        from app.service.metadata_enricher import MAX_SAMPLE_SIZE

        self.assertEqual(
            self.enricher._normalize_sample_size(10 ** 9), MAX_SAMPLE_SIZE)


class LargeTablesGateIsAliveTest(unittest.TestCase):
    """★ 分区裁剪闸门历史上是死的（large_tables 全仓零写入点）。"""

    def setUp(self):
        # 断开物理库：本用例只验网闸逻辑，不该依赖（也不该打扰）线上数据源。
        patcher = patch("app.service.db_service.db_service.real_engine", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _guard(self):
        from app.service.guardrail import Guardrail

        return Guardrail()

    def test_large_tables_has_a_real_write_path(self):
        guard = self._guard()
        self.assertEqual(guard.large_tables, {}, "初始应为空")

        class FakeLayer:
            discovered_table_columns = {
                "dws_trade_order_daily": [("dt", "DATE"), ("gmv", "NUMERIC")],
                "ods_click_log": [("log_date", "DATE"), ("uid", "TEXT")],
                "dim_region": [("region_id", "TEXT"), ("region_name", "TEXT")],
            }

        guard.refresh_large_tables(layer=FakeLayer(), force=True)
        self.assertEqual(guard.large_tables.get("dws_trade_order_daily"), "dt")
        self.assertEqual(guard.large_tables.get("ods_click_log"), "log_date")
        # 没有分区列的维表不该被误判成大表。
        self.assertNotIn("dim_region", guard.large_tables)

    def test_partition_gate_fires_only_without_partition_filter(self):
        guard = self._guard()
        guard.register_large_table("dws_trade_order_daily", "dt")

        def partition_warnings(sql):
            result = guard.check_sql(sql, dialect="mysql")
            return [w for w in result.get("warnings", [])
                    if w.get("rule") == "sql.partition_pruning"]

        missing = partition_warnings(
            "SELECT category_name, SUM(gmv) FROM dws_trade_order_daily "
            "GROUP BY category_name")
        self.assertEqual(len(missing), 1, "大表无分区过滤必须告警")
        self.assertEqual(missing[0]["detail"]["partition_key"], "dt")

        pruned = partition_warnings(
            "SELECT category_name, SUM(gmv) FROM dws_trade_order_daily "
            "WHERE dt >= '2026-09-01' GROUP BY category_name")
        self.assertEqual(pruned, [], "带分区过滤不应告警")

    def test_check_sql_keeps_its_legacy_return_contract(self):
        guard = self._guard()
        result = guard.check_sql("SELECT 1", dialect="mysql")
        for key in ("status", "message", "estimated_rows"):
            self.assertIn(key, result, f"既有返回契约缺少 {key}")


class VectorPointIdentityTest(unittest.TestCase):
    """★ 点 ID 必须由内容派生，重复 ingest 不产生新点。"""

    def test_point_id_is_deterministic_and_scoped(self):
        from app.service.vector_service import derive_point_id

        first = derive_point_id("metrics", "src1", "metric::fact_order::gmv")
        again = derive_point_id("metrics", "src1", "metric::fact_order::gmv")
        self.assertEqual(first, again, "同输入必须得到同一个点 ID")
        self.assertFalse(first.isdigit(), "不能退回自增整数 ID")
        self.assertNotEqual(
            first, derive_point_id("metrics", "src2", "metric::fact_order::gmv"),
            "不同数据源必须落在不同点上")
        self.assertNotEqual(
            first, derive_point_id("metrics", "src1", "metric::fact_other::gmv"),
            "同名指标挂不同物理表不能互相覆盖")


class TracePlumbingTest(unittest.TestCase):
    """trace_id 必须贯到 ask()，且不能破坏既有位置参数调用。"""

    def test_ask_accepts_optional_trace_id(self):
        from app.service.ask_agent import ask_agent

        params = inspect.signature(ask_agent.ask).parameters
        self.assertIn("trace_id", params)
        self.assertIsNone(params["trace_id"].default,
                          "trace_id 必须可选，否则既有调用方全部破坏")
        # 既有契约：前四个位置参数顺序不变。
        self.assertEqual(
            [p for p in params][:4], ["question", "dialect", "user", "role"])

    def test_trace_id_normalisation_blocks_header_injection(self):
        from app.service.run_trace import normalize_trace_id

        self.assertEqual(normalize_trace_id("ok-id_1"), "ok-id_1")
        for bad in ["a\r\nInjected: 1", "x" * 200, "", None, "sp ace", "<script>"]:
            with self.subTest(bad=bad):
                self.assertIsNone(normalize_trace_id(bad))

    def test_backend_and_gateway_agree_on_the_trace_header(self):
        """两侧头名不一致 = 链路在子系统边界上断掉，却不会有任何报错。"""
        import app.service.run_trace as backend_trace

        gateway = os.path.join(
            os.path.dirname(BACKEND_ROOT), "platform_gateway", "tracing.py")
        if not os.path.exists(gateway):
            self.skipTest("本部署不含 platform_gateway")
        with open(gateway, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(f'TRACE_HEADER = "{backend_trace.TRACE_HEADER}"', source)


class AskEndpointConsumesTraceHeaderTest(unittest.TestCase):
    """
    ★ 网关签发的 trace_id 必须真的穿过 HTTP 边界。
    此前 /api/chat/ask 完全没有读 x-trace-id，ask() 每次请求都就地新签一个，
    网关段与问数段的 trace_id 对不上 —— 三级 ID 在最关键的一跳上是断的。
    """

    def _client_and_calls(self):
        from fastapi.testclient import TestClient
        from app.api import chat as chat_api
        from app.main import app

        calls = []

        def fake_ask(question, dialect="doris", user="anonymous", role=None,
                     trace_id=None):
            calls.append(trace_id)
            return {"success": True, "conclusion": "ok",
                    "trace_id": trace_id or "self-issued", "run_id": "run-test"}

        patcher = patch.object(chat_api.ask_agent, "ask", fake_ask)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app), calls

    def test_inbound_trace_header_reaches_ask(self):
        from app.service.run_trace import TRACE_HEADER

        client, calls = self._client_and_calls()
        response = client.post(
            "/api/chat/ask",
            json={"question": "各品类GMV", "dialect": "mysql", "user": "u"},
            headers={TRACE_HEADER: "gw-trace-abc123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["gw-trace-abc123"])
        # 并且要能被调用方看见，否则没法和网关日志对账。
        self.assertEqual(response.json().get("trace_id"), "gw-trace-abc123")

    def test_malicious_trace_header_is_dropped_not_echoed(self):
        from app.service.run_trace import TRACE_HEADER

        client, calls = self._client_and_calls()
        response = client.post(
            "/api/chat/ask",
            json={"question": "各品类GMV", "dialect": "mysql", "user": "u"},
            headers={TRACE_HEADER: "bad id with spaces"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [None], "不合规的头必须被丢弃，由 ask() 另行签发")
        self.assertNotIn("bad id", str(response.json()))

    def test_request_without_trace_header_still_works(self):
        client, calls = self._client_and_calls()
        response = client.post(
            "/api/chat/ask",
            json={"question": "各品类GMV", "dialect": "mysql", "user": "u"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [None], "无头时不能报错，保持既有行为")


class NoUnlabelledFabricatedHistoryTest(unittest.TestCase):
    """
    代码里的伪造 seed 已删除，但历史遗留的两条早就写进了生产数据文件，
    并且不带任何标记 —— 界面上会被当成用户的真实问数历史。
    """

    def _load_history(self):
        path = os.path.join(BACKEND_ROOT, "user_memory.json")
        if not os.path.exists(path):
            self.skipTest("本部署没有落盘的用户记忆文件")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("history", []) if isinstance(data, dict) else data

    def test_code_no_longer_seeds_demo_history_by_default(self):
        from app.model.user_memory import UserMemory

        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=os.path.join(tmp, "user_memory.json"))
        self.assertEqual(memory.history, [],
                         "全新部署必须是空历史，不能塞编造的问数记录")

    def test_persisted_history_has_no_unlabelled_fabricated_record(self):
        for record in self._load_history():
            summary = str(record.get("result_summary", ""))
            if any(fake in summary for fake in FABRICATED_SUMMARIES):
                with self.subTest(record_id=record.get("id")):
                    self.assertTrue(
                        any(marker in summary for marker in DEMO_MARKERS),
                        f"落盘记录 id={record.get('id')} 是编造的演示数据，"
                        f"却没有任何【示例】标记，会被当成真实历史展示: {summary!r}")
                    self.assertTrue(record.get("is_demo"))


if __name__ == "__main__":
    unittest.main()
