import asyncio
import unittest

from app.core.context import set_request_id
from app.core.degradation import InfrastructureFailure
from app.services.query_service import QueryService


class FakeTraceRepository:
    def __init__(self):
        self.calls = []

    async def create_trace(self, trace_id, query_text, user_id=None, **kwargs):
        self.calls.append(("create", {"trace_id": trace_id, **kwargs}))

    async def record_phase(self, **kwargs):
        self.calls.append(("phase", kwargs))

    async def finish_trace(self, **kwargs):
        self.calls.append(("finish", kwargs))


class FakeGraph:
    def __init__(self, events):
        self.events = events
        self.kwargs = None

    async def astream(self, **kwargs):
        self.kwargs = kwargs
        for event in self.events:
            yield event


class InfrastructureFailingGraph:
    async def astream(self, **kwargs):
        if False:
            yield None
        raise InfrastructureFailure(
            "数据仓库暂时不可用，请稍后重试。",
            stage="execution",
            reason="warehouse_unavailable",
        )


def build_service(events, repository=None):
    return QueryService(
        dw_mysql_repository=None,
        meta_mysql_repository=None,
        column_qdrant_repository=None,
        metric_qdrant_repository=None,
        value_es_repository=None,
        embedding_client=None,
        query_trace_repository=repository or FakeTraceRepository(),
        graph_runner=FakeGraph(events),
    )


class QueryProtocolTest(unittest.TestCase):
    def test_sse_events_share_request_id_and_finish_with_done(self):
        service = build_service(
            [
                {"type": "progress", "step": "生成SQL", "status": "running"},
                {
                    "type": "context",
                    "analysis_plan": {"intent": "aggregate"},
                    "query_plan": {
                        "schema_version": "query-plan/v1",
                        "intent": "aggregate",
                        "complexity": "EASY",
                    },
                    "semantic_release_id": "release-2",
                    "semantic_release_version": 2,
                    "query_set_id": "query-set-3",
                    "query_set_version": 3,
                    "business_rule_set_id": "rule-set-4",
                    "business_rule_set_version": 4,
                },
                {"type": "sql", "sql": "SELECT COUNT(*) FROM play_session LIMIT 500"},
                {
                    "type": "result",
                    "sql": "SELECT COUNT(*) FROM play_session LIMIT 500",
                    "columns": ["播放次数"],
                    "data": [{"播放次数": 9}],
                    "row_count": 1,
                    "truncated": False,
                },
                {
                    "type": "answer",
                    "summary": "返回 1 行：播放次数=9。",
                    "metrics": ["play_count"],
                    "time_range": "未限定",
                },
                {
                    "type": "visualization",
                    "chart_spec": {
                        "schema_version": "chart-spec/v1",
                        "type": "kpi",
                        "title": "播放次数",
                        "dimension": None,
                        "metrics": ["播放次数"],
                        "series": None,
                        "source": "deterministic",
                    },
                },
            ]
        )
        set_request_id("request-sse")

        async def run():
            return [event async for event in service.events("播放量是多少")]

        events = asyncio.run(run())

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["status"], "completed")
        self.assertEqual({event["request_id"] for event in events}, {"request-sse"})
        self.assertIn("sql", [event["type"] for event in events])
        recommendations = next(event for event in events if event["type"] == "recommendations")
        self.assertEqual(len(recommendations["questions"]), 3)
        self.assertEqual(recommendations["source"], "manual")
        create = next(
            payload for name, payload in service.query_trace_repository.calls if name == "create"
        )
        self.assertTrue(create["policy_admin_bypass"])
        self.assertEqual(create["policy_version"], "admin-bypass-v1")
        finish = next(payload for name, payload in service.query_trace_repository.calls if name == "finish")
        self.assertEqual(finish["query_plan_summary"]["schema_version"], "query-plan/v1")
        self.assertEqual(finish["query_plan_summary"]["intent"], "aggregate")
        self.assertEqual(finish["answer_summary"], "返回 1 行：播放次数=9。")
        self.assertEqual(finish["chart_spec"]["type"], "kpi")
        self.assertEqual(finish["semantic_release_version"], 2)
        self.assertEqual(finish["query_set_version"], 3)
        self.assertEqual(finish["business_rule_set_version"], 4)

    def test_sync_response_collects_the_same_result_fields(self):
        service = build_service(
            [
                {"type": "sql", "sql": "SELECT id FROM audio_album LIMIT 500"},
                {
                    "type": "result",
                    "sql": "SELECT id FROM audio_album LIMIT 500",
                    "columns": ["专辑ID"],
                    "data": [{"专辑ID": 1}],
                    "row_count": 1,
                    "truncated": False,
                },
                {
                    "type": "answer",
                    "summary": "返回 1 行：专辑ID=1。",
                    "metrics": ["album_count"],
                    "time_range": "本月（2026-07-01 至 2026-07-16）",
                },
                {
                    "type": "visualization",
                    "chart_spec": {
                        "schema_version": "chart-spec/v1",
                        "type": "kpi",
                        "title": "专辑ID",
                        "dimension": None,
                        "metrics": ["专辑ID"],
                        "series": None,
                        "source": "deterministic",
                    },
                },
            ]
        )
        set_request_id("request-sync")

        result = asyncio.run(service.query_sync("专辑数"))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["request_id"], "request-sync")
        self.assertEqual(result["rows"], [{"专辑ID": 1}])
        self.assertEqual(result["metrics"], ["album_count"])
        self.assertEqual(result["chart_spec"]["schema_version"], "chart-spec/v1")
        self.assertIsNone(result["error"])
        self.assertFalse(result["context_inherited"])
        self.assertEqual(result["context_turns_used"], [])
        self.assertEqual(len(result["recommendations"]), 3)

    def test_sync_response_collects_dsl_execution_metadata(self):
        service = build_service(
            [
                {
                    "type": "context",
                    "generation_mode": "dsl",
                    "generation_source": "dsl_compiled",
                    "query_dsl": {"version": "1", "intent": "aggregate"},
                    "dsl_attempts": 1,
                    "llm_calls": 1,
                    "query_set_id": "query-set-3",
                    "query_set_version": 3,
                    "query_set_hash": "a" * 64,
                    "verified_query_match": {"case_key": "play_count"},
                    "business_rule_matches": [
                        {
                            "rule_key": "exclude_test_plays",
                            "version": 2,
                            "rule_type": "metric_constraint",
                        }
                    ],
                    "planning_roles": ["Selector", "Refiner"],
                    "selected_semantics": {
                        "metric_ids": ["play_count"],
                        "field_ids": [],
                        "table_ids": ["play_session"],
                        "relationship_ids": [],
                    },
                    "decomposed_query": [],
                    "query_plan_refined": True,
                    "dry_plan_status": "validated",
                    "dry_plan_checks": ["schema_version", "table_acl"],
                    "sql_validation_stages": [
                        "ast_permissions",
                        "rls_injection",
                        "post_rls_ast",
                        "explain_cost",
                        "read_only_timeout",
                    ],
                    "explain_estimate": {
                        "estimated_cost": 12,
                        "estimated_rows": 10,
                        "source": "mysql:rows",
                    },
                    "execution_mode": "read_only",
                    "execution_timeout_seconds": 30,
                    "query_plan": {
                        "schema_version": "query-plan/v1",
                        "intent": "aggregate",
                        "complexity": "EASY",
                    },
                },
                {"type": "done", "status": "completed", "duration_ms": 12, "error": None},
            ]
        )
        set_request_id("request-dsl-sync")

        result = asyncio.run(service.query_sync("播放量是多少", parameters={"p1": 7}))

        self.assertEqual(result["generation_mode"], "dsl")
        self.assertEqual(result["generation_source"], "dsl_compiled")
        self.assertEqual(result["query_dsl"]["version"], "1")
        self.assertEqual(result["dsl_attempts"], 1)
        self.assertEqual(result["llm_calls"], 1)
        self.assertEqual(result["query_set_version"], 3)
        self.assertEqual(result["verified_query_match"]["case_key"], "play_count")
        self.assertEqual(
            result["business_rule_matches"][0]["rule_key"], "exclude_test_plays"
        )
        self.assertEqual(result["query_plan"]["schema_version"], "query-plan/v1")
        self.assertEqual(result["planning_roles"], ["Selector", "Refiner"])
        self.assertTrue(result["query_plan_refined"])
        self.assertEqual(result["dry_plan_status"], "validated")
        self.assertEqual(result["sql_validation_stages"][-1], "read_only_timeout")
        self.assertEqual(result["explain_estimate"]["estimated_cost"], 12)
        self.assertEqual(result["execution_mode"], "read_only")
        self.assertEqual(service.graph_runner.kwargs["input"]["query_parameters"], {"p1": 7})

    def test_error_event_is_followed_by_failed_done_event(self):
        service = build_service(
            [{"type": "error", "stage": "sql_validation", "message": "字段未授权"}]
        )
        set_request_id("request-error")

        async def run():
            return [event async for event in service.events("查手机号")]

        events = asyncio.run(run())

        self.assertEqual(events[-2]["type"], "error")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["status"], "failed")
        self.assertEqual(events[-1]["error"], "字段未授权")

    def test_infrastructure_failure_is_failed_without_result_or_answer(self):
        service = build_service([])
        service.graph_runner = InfrastructureFailingGraph()
        set_request_id("request-infra-failed")

        async def run():
            return [event async for event in service.events("播放量是多少")]

        events = asyncio.run(run())

        self.assertEqual(events[-2]["type"], "error")
        self.assertEqual(events[-2]["reason"], "warehouse_unavailable")
        self.assertEqual(events[-1]["status"], "failed")
        self.assertFalse(any(event["type"] in {"result", "answer"} for event in events))

    def test_sse_serialization_uses_event_name_and_json_data(self):
        service = build_service([{"type": "progress", "step": "分析问题", "status": "success"}])
        set_request_id("request-frame")

        async def run():
            return [frame async for frame in service.query("播放量")]

        frames = asyncio.run(run())

        self.assertTrue(all(frame.startswith("event: ") for frame in frames))
        self.assertTrue(all("\ndata: {" in frame for frame in frames))
        self.assertTrue(frames[-1].startswith("event: done"))

    def test_internal_trace_sql_is_persisted_but_not_streamed(self):
        repository = FakeTraceRepository()
        service = build_service(
            [
                {
                    "type": "trace_sql",
                    "sql": "SELECT missing_column FROM play_session",
                    "status": "generated",
                },
                {"type": "error", "message": "字段不存在"},
            ],
            repository=repository,
        )
        set_request_id("request-failed-sql")

        async def run():
            return [frame async for frame in service.query("查询播放量")]

        frames = asyncio.run(run())

        self.assertNotIn("trace_sql", "".join(frames))
        finish = next(payload for name, payload in repository.calls if name == "finish")
        self.assertEqual(finish["sql"], "SELECT missing_column FROM play_session")
        self.assertEqual(finish["status"], "failed")

    def test_closing_stream_records_cancelled_trace_with_reason(self):
        repository = FakeTraceRepository()
        service = build_service([], repository=repository)
        set_request_id("request-cancelled")

        async def run():
            stream = service.query("播放量是多少")
            first_frame = await anext(stream)
            await stream.aclose()
            return first_frame

        first_frame = asyncio.run(run())

        self.assertTrue(first_frame.startswith("event: context"))
        finish = next(payload for name, payload in repository.calls if name == "finish")
        self.assertEqual(finish["status"], "cancelled")
        self.assertIn("连接已断开", finish["error_message"])


if __name__ == "__main__":
    unittest.main()
