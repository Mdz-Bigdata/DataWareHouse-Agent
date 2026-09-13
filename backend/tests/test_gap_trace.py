# -*- coding: utf-8 -*-
"""全链路追溯 ID 贯通 + 分阶段耗时度量的回归测试。

覆盖三件事：
1. trace_id 从网关签发/校验，到 ``ask_agent.ask()`` 被真正消费；
2. 一次问数产出一条运行记录（问题、DSL、SQL、分阶段耗时、命中的网闸、最终状态）；
3. Qdrant 召回 / LLM 生成 / 网闸校验 / 重试 各自独立计时，而不是只有一个总耗时。
"""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DB_TYPE", "sqlite")

import httpx
from fastapi.testclient import TestClient

from app.model.user_memory import user_memory
from app.service.ask_agent import ask_agent
from app.service.db_service import DBService
from app.service.guardrail import GuardrailException
from app.service.run_trace import (
    RunRecord,
    RunTraceRegistry,
    current_trace_id,
    normalize_trace_id,
    resolve_trace_id,
    run_trace_registry,
    trace_scope,
)
from app.service.semantic_cache import semantic_cache

# 网关与 backend 是两个部署单元，测试里把仓库根目录挂上来才能一起验。
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    # 追加而非插到最前，避免仓库根目录的同名模块盖掉 backend 自己的包。
    sys.path.append(REPO_ROOT)

from platform_gateway import main as gateway_main  # noqa: E402
from platform_gateway.capabilities import CapabilityRegistry, Subsystem  # noqa: E402
from platform_gateway.proxy import forwarded_headers  # noqa: E402
from platform_gateway.tracing import GatewayTraceLog  # noqa: E402
from platform_gateway.tracing import normalize_trace_id as gateway_normalize  # noqa: E402
from platform_gateway.tracing import resolve_trace_id as gateway_resolve  # noqa: E402


class TraceIdRulesTest(unittest.TestCase):
    """追溯 ID 来自 HTTP 头，是外部输入：合规的照单全收，不合规的一律重签。"""

    def test_normalize_accepts_plain_identifiers(self):
        for value in ("abc123", "trace-1", "a.b_c:d", "f" * 64):
            self.assertEqual(normalize_trace_id(value), value)

    def test_normalize_rejects_unsafe_or_oversized_values(self):
        for value in ("", "   ", "bad id", "trace\nx-injected: 1", "f" * 65, None, 42, ["t"]):
            self.assertIsNone(normalize_trace_id(value), value)

    def test_normalize_trims_surrounding_whitespace_and_never_returns_control_chars(self):
        self.assertEqual(normalize_trace_id("  trace-1  "), "trace-1")
        # 尾部 CRLF 先被裁掉，剩下的部分仍要过一遍白名单校验，故不可能夹带注入。
        self.assertEqual(normalize_trace_id("trace\r\n"), "trace")
        for value in ("  trace-1  ", "trace\r\n", "\ttrace-2\t"):
            resolved = normalize_trace_id(value)
            self.assertNotIn("\r", resolved)
            self.assertNotIn("\n", resolved)

    def test_resolve_prefers_argument_then_context_then_mints_new(self):
        self.assertEqual(resolve_trace_id("trace-1"), "trace-1")
        with trace_scope("ctx-trace"):
            self.assertEqual(current_trace_id(), "ctx-trace")
            self.assertEqual(resolve_trace_id(None), "ctx-trace")
            # 显式入参优先于上下文。
            self.assertEqual(resolve_trace_id("explicit"), "explicit")
            # 不合规入参不会污染链路，退回上下文值。
            self.assertEqual(resolve_trace_id("bad id"), "ctx-trace")
        self.assertIsNone(current_trace_id())
        minted = resolve_trace_id(None)
        self.assertEqual(normalize_trace_id(minted), minted)

    def test_trace_scope_restores_previous_binding(self):
        with trace_scope("outer"):
            with trace_scope("inner"):
                self.assertEqual(current_trace_id(), "inner")
            self.assertEqual(current_trace_id(), "outer")

    def test_gateway_and_backend_agree_on_what_a_valid_trace_id_is(self):
        for value in ("abc123", "trace-1", "a.b_c:d"):
            self.assertEqual(gateway_normalize(value), normalize_trace_id(value))
        for value in ("bad id", "trace\nx: 1", "f" * 65, ""):
            self.assertIsNone(gateway_normalize(value))
            self.assertIsNone(normalize_trace_id(value))


class RunRecordTest(unittest.TestCase):
    def test_stage_records_duration_and_order(self):
        record = RunRecord(trace_id="trace-1", question="q", user="u", role="admin", dialect="doris")
        with record.stage("qdrant_recall", meta_count=3):
            pass
        with record.stage("llm_dsl"):
            pass
        self.assertEqual([item.name for item in record.stages], ["qdrant_recall", "llm_dsl"])
        self.assertEqual([item.seq for item in record.stages], [1, 2])
        self.assertTrue(all(item.duration_ms >= 0 for item in record.stages))
        self.assertEqual(record.stages[0].meta["meta_count"], 3)

    def test_failing_stage_is_recorded_and_the_exception_still_propagates(self):
        record = RunRecord(trace_id="trace-1")
        with self.assertRaises(GuardrailException):
            with record.stage("guardrail_sql"):
                raise GuardrailException("性能熔断拦截")
        stage = record.stages[0]
        self.assertFalse(stage.ok)
        self.assertIn("GuardrailException", stage.error)
        self.assertGreaterEqual(stage.duration_ms, 0)

    def test_repeated_stage_names_accumulate_per_attempt(self):
        record = RunRecord()
        record.add_stage("db_execute", 5.0, attempt=1)
        record.add_stage("db_execute", 7.0, ok=False, attempt=2)
        self.assertEqual(record.stage_ms("db_execute"), 12.0)
        self.assertEqual([item.meta["attempt"] for item in record.stages], [1, 2])

    def test_long_fields_are_clipped_so_records_stay_bounded(self):
        record = RunRecord(question="问" * 5000)
        record.set_sql("SELECT " + "x" * 5000)
        self.assertLess(len(record.question), 5000)
        self.assertLess(len(record.sql), 5000)
        self.assertTrue(record.question.endswith("[truncated]"))

    def test_recorded_dsl_snapshot_is_not_mutated_by_later_writes(self):
        record = RunRecord()
        dsl = {"metrics": [{"name": "total_gmv"}]}
        record.set_dsl(dsl)
        dsl["metrics"] = []
        self.assertEqual(record.dsl["metrics"], [{"name": "total_gmv"}])

    def test_finish_freezes_status_and_total(self):
        record = RunRecord(trace_id="trace-1")
        record.finish("success")
        payload = record.to_dict()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["trace_id"], "trace-1")
        self.assertGreaterEqual(payload["total_ms"], 0)


class RunTraceRegistryTest(unittest.TestCase):
    def test_lookup_by_run_id_and_trace_id(self):
        registry = RunTraceRegistry(max_records=10)
        first = registry.start_run(trace_id="trace-a", question="q1")
        second = registry.start_run(trace_id="trace-a", question="q2")
        registry.start_run(trace_id="trace-b", question="q3")
        self.assertIs(registry.get(first.run_id), first)
        self.assertEqual([item.run_id for item in registry.for_trace("trace-a")],
                         [first.run_id, second.run_id])
        self.assertIsNone(registry.get("run-does-not-exist"))

    def test_one_run_id_per_execution(self):
        registry = RunTraceRegistry()
        ids = {registry.start_run(trace_id="trace-a").run_id for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_registry_is_bounded_and_keeps_the_newest(self):
        registry = RunTraceRegistry(max_records=3)
        runs = [registry.start_run(trace_id=f"t{index}") for index in range(5)]
        self.assertEqual(len(registry), 3)
        self.assertIsNone(registry.get(runs[0].run_id))
        self.assertIs(registry.get(runs[4].run_id), runs[4])
        self.assertEqual(registry.recent(1)[0].run_id, runs[4].run_id)

    def test_record_does_not_duplicate_an_already_registered_run(self):
        registry = RunTraceRegistry()
        run = registry.start_run(trace_id="trace-a")
        registry.record(run)
        registry.record(run)
        self.assertEqual(len(registry), 1)


class AskTraceTest(unittest.TestCase):
    """离线（演示数仓 + 确定性降级）跑通主链路，检查运行记录与分阶段耗时。"""

    USER = "trace-regression"

    def setUp(self):
        self.db = DBService()
        self.addCleanup(self.db.conn.close)
        db_module = importlib.import_module("app.service.db_service")
        self.ask_module = importlib.import_module("app.service.ask_agent")
        attribution_module = importlib.import_module("app.service.skills.attribution_skill")
        semantic_module = importlib.import_module("app.service.semantic_layer")
        for target in (db_module, self.ask_module, attribution_module):
            patcher = patch.object(target, "db_service", self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        layer = semantic_module.SemanticLayer()
        for target in (semantic_module, attribution_module, ask_agent):
            patcher = patch.object(target, "semantic_layer", layer)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.dict(os.environ, {"MOCK_LLM": "false"}),
                        patch.object(user_memory, "_save"),
                        patch.object(ask_agent, "_call_llm",
                                     side_effect=AssertionError("LLM must not be used"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        semantic_cache.invalidate_all()
        self.addCleanup(semantic_cache.invalidate_all)
        ask_agent.user_sessions.pop(self.USER, None)
        ask_agent.user_history_questions.pop(self.USER, None)
        run_trace_registry.clear()

    def ask(self, question, **kwargs):
        kwargs.setdefault("user", self.USER)
        kwargs.setdefault("role", "admin")
        return ask_agent.ask(question, **kwargs)

    def test_gateway_trace_id_reaches_the_response_and_the_run_record(self):
        result = self.ask("昨天总交易额是多少", trace_id="gw-trace-1")
        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(result["trace_id"], "gw-trace-1")
        self.assertEqual(result["details"]["trace_id"], "gw-trace-1")
        record = run_trace_registry.get(result["run_id"])
        self.assertIsNotNone(record)
        self.assertEqual(record.trace_id, "gw-trace-1")
        self.assertEqual([item.run_id for item in run_trace_registry.for_trace("gw-trace-1")],
                         [result["run_id"]])

    def test_ask_stays_callable_without_a_trace_id_and_mints_one(self):
        # 既有调用方（chat.py 等）不传新参数也必须照常工作。
        result = ask_agent.ask("昨天总交易额是多少", "doris", self.USER, "admin")
        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(normalize_trace_id(result["trace_id"]), result["trace_id"])
        self.assertTrue(result["run_id"].startswith("run-"))

    def test_context_bound_trace_id_is_used_when_no_argument_is_passed(self):
        with trace_scope("ctx-trace-9"):
            result = self.ask("昨天总交易额是多少")
        self.assertEqual(result["trace_id"], "ctx-trace-9")

    def test_one_question_is_one_run_record_with_the_full_query_story(self):
        result = self.ask("各品类最近30天交易额", trace_id="gw-trace-2")
        record = run_trace_registry.get(result["run_id"]).to_dict()
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["question"], "各品类最近30天交易额")
        self.assertEqual(record["user"], self.USER)
        self.assertEqual(record["role"], "admin")
        self.assertEqual(record["dialect"], "doris")
        self.assertIn("SELECT", record["sql"])
        self.assertTrue(record["dsl"]["metrics"])
        self.assertGreaterEqual(record["row_count"], 0)
        self.assertGreater(record["total_ms"], 0)
        self.assertEqual(record["retry_count"], 0)

    def test_each_phase_is_timed_separately_not_just_the_total(self):
        result = self.ask("各品类最近30天交易额")
        record = run_trace_registry.get(result["run_id"])
        stages = {item["stage"]: item for item in result["details"]["stage_timings"]}
        for phase in ("embedding", "cache_lookup", "qdrant_recall", "skill_route",
                      "deterministic_fallback", "session_merge", "guardrail_dsl",
                      "sql_compile", "guardrail_sql", "db_execute", "format_sql",
                      "render_chart"):
            self.assertIn(phase, stages, f"缺少 {phase} 阶段计时")
            self.assertGreaterEqual(stages[phase]["duration_ms"], 0.0)
        # 分阶段耗时之和不该超过整体耗时（各阶段互不重叠）。
        self.assertLessEqual(sum(item["duration_ms"] for item in stages.values()) - 1.0,
                             record.total_ms)
        self.assertEqual([item["seq"] for item in result["details"]["stage_timings"]],
                         sorted(item["seq"] for item in result["details"]["stage_timings"]))

    def test_guardrail_outcomes_are_recorded_on_the_run(self):
        result = self.ask("各品类最近30天交易额")
        record = run_trace_registry.get(result["run_id"])
        layers = {item["layer"]: item for item in record.guardrails}
        self.assertEqual(layers["dsl"]["outcome"], "pass")
        self.assertEqual(layers["sql"]["outcome"], "pass")
        self.assertIn("estimated_rows", layers["sql"])

    def test_dsl_guardrail_block_is_recorded_and_still_traceable(self):
        blocked = GuardrailException("权限拦截: 当前角色无权访问手机号字段")
        with patch.object(self.ask_module.guardrail, "check_dsl", side_effect=blocked):
            result = self.ask("各品类最近30天交易额", trace_id="gw-trace-3")
        self.assertFalse(result["success"])
        record = run_trace_registry.get(result["run_id"])
        self.assertEqual(record.status, "guardrail_blocked")
        self.assertEqual([(item["layer"], item["outcome"]) for item in record.guardrails],
                         [("dsl", "block")])
        self.assertIn("手机号", record.guardrails[0]["message"])
        self.assertEqual(result["details"]["trace_id"], "gw-trace-3")
        self.assertFalse(record.stages[-1].ok)
        # 被网闸挡下就不该再有编译与执行阶段。
        self.assertFalse({"sql_compile", "db_execute"} & {item.name for item in record.stages})

    def test_semantic_audit_clarification_branch_is_also_traceable(self):
        blocked = GuardrailException("语义审计拦截: 当前角色无权访问该维度")
        with patch.object(self.ask_module.guardrail, "check_dsl", side_effect=blocked):
            result = self.ask("各品类最近30天交易额", trace_id="gw-trace-10")
        self.assertFalse(result["success"])
        record = run_trace_registry.get(result["run_id"])
        self.assertEqual(record.status, "clarification")
        self.assertEqual(result["details"]["trace_id"], "gw-trace-10")
        self.assertEqual(result["details"]["run_id"], record.run_id)
        self.assertTrue(result["details"]["stage_timings"])

    def test_llm_generation_phases_are_timed_on_their_own(self):
        with patch.dict(os.environ, {"MOCK_LLM": "true"}), \
             patch.object(ask_agent, "_call_llm", return_value="总的来看，数据正常。") as llm:
            result = self.ask("昨天总交易额是多少", trace_id="gw-trace-11")
        self.assertTrue(result["success"], result.get("error"))
        stages = {item["stage"] for item in result["details"]["stage_timings"]}
        self.assertIn("llm_dsl", stages)
        self.assertIn("llm_conclusion", stages)
        record = run_trace_registry.get(result["run_id"])
        tiers = {item.name: item.meta.get("model_tier") for item in record.stages
                 if item.name.startswith("llm_")}
        self.assertEqual(tiers["llm_conclusion"], "complex")
        self.assertIn(tiers["llm_dsl"], {"fast", "complex"})
        self.assertGreater(llm.call_count, 0)

    def test_clarification_run_is_still_traceable(self):
        result = self.ask("食堂消费额是多少", trace_id="gw-trace-4")
        self.assertFalse(result["success"])
        record = run_trace_registry.get(result["run_id"])
        self.assertEqual(record.status, "clarification")
        self.assertEqual(record.trace_id, "gw-trace-4")
        self.assertTrue(record.error)
        self.assertIn("stage_timings", result["details"])

    def test_cache_hit_is_its_own_run_and_never_reuses_the_previous_trace(self):
        first = self.ask("昨天总交易额是多少", trace_id="gw-trace-5")
        second = self.ask("昨天总交易额是多少", trace_id="gw-trace-6")
        self.assertTrue(second.get("cache_hit"), second)
        self.assertNotEqual(second["run_id"], first["run_id"])
        self.assertEqual(second["trace_id"], "gw-trace-6")
        self.assertEqual(second["details"]["trace_id"], "gw-trace-6")
        record = run_trace_registry.get(second["run_id"])
        self.assertEqual(record.status, "cache_hit")
        self.assertIn(record.cache_hit, {"exact", "semantic"})
        self.assertTrue(any(item["stage"] == "cache_lookup" for item in
                            second["details"]["stage_timings"]))

    def test_cached_payload_does_not_carry_a_stale_trace_id(self):
        self.ask("昨天总交易额是多少", trace_id="gw-trace-7")
        cached = semantic_cache.get("昨天总交易额是多少", dialect="doris", role="admin")
        self.assertIsNotNone(cached)
        self.assertNotIn("trace_id", cached[0].get("details", {}))

    def test_unexpected_crash_still_closes_the_run_record(self):
        with patch.object(self.ask_module.guardrail, "check_dsl", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.ask("昨天总交易额是多少", trace_id="gw-trace-8")
        record = run_trace_registry.recent(1)[0]
        self.assertEqual(record.trace_id, "gw-trace-8")
        self.assertEqual(record.status, "crashed")
        self.assertIn("boom", record.error)
        self.assertFalse(record.stages[-1].ok)

    def test_sql_guardrail_block_times_every_retry_attempt(self):
        blocked = GuardrailException("性能熔断拦截: 预估扫描行数超过安全阈值")
        with patch.dict(os.environ, {"MOCK_LLM": "true"}), \
             patch.object(self.ask_module.guardrail, "check_sql", side_effect=blocked), \
             patch.object(ask_agent, "_call_llm", return_value="SELECT 1"):
            result = self.ask("昨天总交易额是多少", trace_id="gw-trace-9")
        self.assertFalse(result["success"])
        record = run_trace_registry.get(result["run_id"])
        self.assertEqual(record.status, "execution_error")
        attempts = [item.meta.get("attempt") for item in record.stages if item.name == "guardrail_sql"]
        self.assertEqual(attempts, [1, 2, 3])
        self.assertTrue(all(item.name != "db_execute" for item in record.stages))
        self.assertEqual(record.retry_count, 3)
        self.assertEqual([item.meta.get("attempt") for item in record.stages
                          if item.name == "llm_sql_repair"], [1, 2])
        self.assertEqual([item["outcome"] for item in record.guardrails if item["layer"] == "sql"],
                         ["block", "block", "block"])


class GatewayTraceTest(unittest.TestCase):
    """网关是链路第一站：签发 trace_id、透传下游、回写响应头并记下这一段耗时。"""

    class _Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"ok": true}'

    def _client(self, upstream):
        transport = gateway_main.create_http_client(transport=httpx.MockTransport(upstream))
        registry = CapabilityRegistry([Subsystem("core", "Core", "/platform/core", "http://core")])
        gateway_main.trace_log.clear()
        self.addCleanup(gateway_main.trace_log.clear)
        return patch.object(gateway_main, "registry", registry), \
            patch.object(gateway_main, "create_http_client", return_value=transport)

    def test_resolve_uses_a_valid_inbound_header_and_replaces_a_malformed_one(self):
        self.assertEqual(gateway_resolve({"x-trace-id": "trace-1"}), "trace-1")
        for bad in ("bad id", "x" * 65, ""):
            resolved = gateway_resolve({"x-trace-id": bad})
            self.assertNotEqual(resolved, bad)
            self.assertEqual(gateway_normalize(resolved), resolved)
        minted = gateway_resolve({})
        self.assertEqual(gateway_normalize(minted), minted)

    def test_forwarded_headers_still_carry_the_trace_to_the_subsystem(self):
        headers = forwarded_headers({"x-trace-id": "client-supplied"}, trace_id="trace-1")
        self.assertEqual(headers["x-trace-id"], "trace-1")

    def test_forwarded_headers_never_inject_a_malformed_trace_id(self):
        for bad in ("bad id", "trace\nx-injected: 1", "x" * 65, ""):
            value = forwarded_headers({}, trace_id=bad)["x-trace-id"]
            self.assertNotEqual(value, bad)
            self.assertEqual(gateway_normalize(value), value)

    def test_proxy_propagates_one_trace_id_downstream_and_back(self):
        seen = {}

        def upstream(request):
            seen["trace"] = request.headers.get("x-trace-id")
            return httpx.Response(200, stream=self._Stream())

        with self._client(upstream)[0], self._client(upstream)[1]:
            with TestClient(gateway_main.app) as client:
                response = client.get("/api/platform/core/api/chat/history?user=a",
                                      headers={"x-trace-id": "trace-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["trace"], "trace-1")
        self.assertEqual(response.headers["x-trace-id"], "trace-1")
        self.assertGreaterEqual(float(response.headers["x-gateway-upstream-ms"]), 0.0)

    def test_proxy_replaces_a_malformed_inbound_trace_id(self):
        seen = {}

        def upstream(request):
            seen["trace"] = request.headers.get("x-trace-id")
            return httpx.Response(200, stream=self._Stream())

        patches = self._client(upstream)
        with patches[0], patches[1]:
            with TestClient(gateway_main.app) as client:
                response = client.get("/api/platform/core/health", headers={"x-trace-id": "bad id"})
        self.assertNotEqual(seen["trace"], "bad id")
        self.assertEqual(gateway_normalize(seen["trace"]), seen["trace"])
        self.assertEqual(response.headers["x-trace-id"], seen["trace"])

    def test_proxy_records_the_gateway_leg_without_the_query_string(self):
        def upstream(request):
            return httpx.Response(201, stream=self._Stream())

        patches = self._client(upstream)
        with patches[0], patches[1]:
            with TestClient(gateway_main.app) as client:
                client.post("/api/platform/core/api/chat/ask?question=secret",
                            headers={"x-trace-id": "trace-7"}, json={"question": "机密问句"})
        items = gateway_main.trace_log.for_trace("trace-7")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["subsystem"], "core")
        self.assertEqual(items[0]["method"], "POST")
        self.assertEqual(items[0]["status_code"], 201)
        self.assertEqual(items[0]["path"], "/api/platform/core/api/chat/ask")
        self.assertNotIn("secret", items[0]["path"])
        self.assertGreaterEqual(items[0]["elapsed_ms"], 0.0)

    def test_unreachable_upstream_is_recorded_as_502_on_the_same_trace(self):
        def upstream(request):
            raise httpx.ConnectError("refused")

        patches = self._client(upstream)
        with patches[0], patches[1]:
            with TestClient(gateway_main.app) as client:
                response = client.get("/api/platform/core/health", headers={"x-trace-id": "trace-8"})
        self.assertEqual(response.status_code, 502)
        items = gateway_main.trace_log.for_trace("trace-8")
        self.assertEqual([item["status_code"] for item in items], [502])
        self.assertEqual(items[0]["error"], "ConnectError")

    def test_trace_log_is_bounded_and_drops_the_oldest(self):
        log = GatewayTraceLog(max_records=2)
        for index in range(4):
            log.record(trace_id=f"t{index}", subsystem="core", method="GET",
                       path="/x", status_code=200, elapsed_ms=1.0)
        self.assertEqual(len(log), 2)
        self.assertEqual([item["trace_id"] for item in log.recent(10)], ["t3", "t2"])

    def test_trace_log_clips_an_oversized_path(self):
        log = GatewayTraceLog()
        log.record(trace_id="t", subsystem="core", method="GET", path="/" + "p" * 5000,
                   status_code=200, elapsed_ms=1.0)
        self.assertLessEqual(len(log.recent(1)[0]["path"]), 200)

    def test_trace_endpoint_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLATFORM_TRACE_ENDPOINT", None)
            with TestClient(gateway_main.app) as client:
                self.assertEqual(client.get("/api/platform/traces").status_code, 404)

    def test_trace_endpoint_returns_records_when_explicitly_enabled(self):
        gateway_main.trace_log.clear()
        self.addCleanup(gateway_main.trace_log.clear)
        gateway_main.trace_log.record(trace_id="trace-9", subsystem="core", method="GET",
                                      path="/api/platform/core/health", status_code=200,
                                      elapsed_ms=2.5)
        with patch.dict(os.environ, {"PLATFORM_TRACE_ENDPOINT": "1"}):
            with TestClient(gateway_main.app) as client:
                payload = client.get("/api/platform/traces?trace_id=trace-9").json()
        self.assertEqual([item["trace_id"] for item in payload["items"]], ["trace-9"])
        self.assertEqual(payload["trace_header"], "x-trace-id")


if __name__ == "__main__":
    unittest.main()
