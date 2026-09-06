import asyncio
import unittest

from app.services.health_service import readiness_report
from app.services.query_trace_service import QueryTraceRecorder


class FakeTraceRepository:
    def __init__(self):
        self.calls = []

    async def create_trace(self, trace_id, query_text, user_id=None, **kwargs):
        self.calls.append(("create", trace_id, query_text))

    async def record_phase(self, **kwargs):
        self.calls.append(("phase", kwargs))

    async def finish_trace(self, **kwargs):
        self.calls.append(("finish", kwargs))


class ObservabilityTest(unittest.TestCase):
    def test_readiness_aggregates_dependency_results_without_error_details(self):
        async def ok():
            return None

        async def broken():
            raise ConnectionError("password=should-not-leak")

        report = asyncio.run(readiness_report({"mysql": ok, "qdrant": broken}))

        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["dependencies"]["mysql"]["status"], "ok")
        self.assertEqual(report["dependencies"]["qdrant"]["detail"], "ConnectionError")
        self.assertNotIn("password", str(report))

    def test_trace_records_phases_sql_and_error_but_never_rows(self):
        repository = FakeTraceRepository()

        async def run():
            recorder = QueryTraceRecorder(repository, "trace-1", "播放量是多少")
            await recorder.start()
            await recorder.observe({"type": "progress", "step": "生成SQL", "status": "running"})
            await recorder.observe(
                {
                    "type": "trace_sql",
                    "sql": "SELECT missing_column FROM play_session",
                    "status": "generated",
                }
            )
            await recorder.observe(
                {
                    "type": "progress",
                    "step": "生成SQL",
                    "status": "success",
                    "duration_ms": 17,
                }
            )
            await recorder.observe({"type": "progress", "step": "校验SQL", "status": "running"})
            await recorder.observe(
                {
                    "type": "progress",
                    "step": "校验SQL",
                    "status": "error",
                    "duration_ms": 3,
                    "message": "字段未授权",
                    "sql": "SELECT missing_column FROM play_session",
                }
            )
            await recorder.observe({"type": "context", "build_id": "build-1"})
            await recorder.observe(
                {
                    "type": "result",
                    "sql": "SELECT COUNT(*) FROM play_session",
                    "data": [{"播放量": 99}],
                }
            )
            await recorder.finish()

        asyncio.run(run())

        finish = repository.calls[-1][1]
        self.assertEqual(finish["sql"], "SELECT COUNT(*) FROM play_session")
        self.assertEqual(finish["build_id"], "build-1")
        self.assertNotIn("data", str(repository.calls))
        self.assertIn("phase", [item[0] for item in repository.calls])
        phases = [item[1] for item in repository.calls if item[0] == "phase"]
        self.assertEqual(phases[0]["sql"], "SELECT missing_column FROM play_session")
        self.assertEqual(phases[0]["duration_ms"], 17)
        self.assertEqual(phases[1]["error_message"], "字段未授权")
        self.assertEqual(phases[1]["sql"], "SELECT missing_column FROM play_session")

    def test_trace_keeps_last_failed_sql_attempt(self):
        repository = FakeTraceRepository()

        async def run():
            recorder = QueryTraceRecorder(repository, "trace-failed", "查询播放量")
            await recorder.start()
            await recorder.observe({"type": "trace_sql", "sql": "SELECT bad_a FROM play_session"})
            await recorder.observe({"type": "trace_sql", "sql": "SELECT bad_b FROM play_session"})
            await recorder.finish("字段 bad_b 不存在")

        asyncio.run(run())

        finish = repository.calls[-1][1]
        self.assertEqual(finish["status"], "failed")
        self.assertEqual(finish["sql"], "SELECT bad_b FROM play_session")
        self.assertEqual(finish["error_message"], "字段 bad_b 不存在")


if __name__ == "__main__":
    unittest.main()
