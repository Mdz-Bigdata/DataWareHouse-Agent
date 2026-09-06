"""Phase 4.2：基础设施降级判断测试。"""

import asyncio
import unittest
from types import SimpleNamespace

from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.validate_sql import validate_sql
from app.core.degradation import (
    InfrastructureFailure,
    degradation_message,
    is_infra_failure,
    is_sql_semantic_failure,
)


class InfraFailureDetectionTest(unittest.TestCase):
    """验证基础设施故障的识别准确性。"""

    def test_detects_operational_error(self):
        # SQLAlchemy OperationalError 是典型的基础设施故障
        exc = OperationalErrorMock("(2003, 'Can\\'t connect to MySQL server')")
        self.assertTrue(is_infra_failure(exc))

    def test_detects_connection_refused(self):
        exc = ConnectionRefusedError("Connection refused")
        self.assertTrue(is_infra_failure(exc))

    def test_detects_timeout_error(self):
        exc = TimeoutError("Connection timed out")
        self.assertTrue(is_infra_failure(exc))

    def test_detects_by_message_keyword(self):
        # 异常类名不匹配，但消息含关键词
        exc = RuntimeError("server has gone away")
        self.assertTrue(is_infra_failure(exc))

    def test_detects_pool_exhausted(self):
        exc = RuntimeError("too many connections")
        self.assertTrue(is_infra_failure(exc))

    def test_not_infra_for_sql_syntax_error(self):
        # SQL 语法错误不是基础设施故障，应走修复流程
        exc = ValueError("SQL 语法无法解析")
        self.assertFalse(is_infra_failure(exc))

    def test_operational_error_with_sql_code_is_semantic(self):
        exc = OperationalErrorMock(1054, "Unknown column 'missing'")
        self.assertTrue(is_sql_semantic_failure(exc))
        self.assertFalse(is_infra_failure(exc))

    def test_not_infra_for_generic_value_error(self):
        exc = ValueError("max_result_rows 必须大于 0")
        self.assertFalse(is_infra_failure(exc))

    def test_not_infra_for_key_error(self):
        exc = KeyError("missing_field")
        self.assertFalse(is_infra_failure(exc))

    def test_degradation_message_no_sensitive_info(self):
        # 降级提示不应包含异常细节（避免泄露连接信息）
        exc = ConnectionRefusedError("mysql://user:password@10.0.0.1:3306")
        msg = degradation_message(exc)
        self.assertIn("数据仓库", msg)
        self.assertNotIn("password", msg)
        self.assertNotIn("10.0.0.1", msg)
        self.assertNotIn("3306", msg)


class OperationalErrorMock(Exception):
    """模拟 SQLAlchemy OperationalError 的最小桩。"""

    pass


class SQLNodeFailureRoutingTest(unittest.TestCase):
    def _state(self):
        return {
            "sql": "SELECT id FROM audio_album",
            "table_infos": [{"name": "audio_album", "columns": [{"name": "id"}]}],
            "relationships": [],
            "row_level_scope": [],
            "access_policy": {},
            "analysis_plan": {},
            "db_info": {"dialect": "mysql"},
            "correction_attempts": 0,
        }

    def test_validation_sql_error_returns_refiner_state(self):
        class Warehouse:
            async def validate_sql(self, sql, timeout_seconds):
                raise OperationalErrorMock(1054, "Unknown column 'missing'")

        events = []
        runtime = SimpleNamespace(
            context={"dw_mysql_repository": Warehouse()},
            stream_writer=events.append,
        )
        result = asyncio.run(validate_sql(self._state(), runtime))

        self.assertEqual(result["error_kind"], "sql_semantic")
        self.assertEqual(result["error_stage"], "sql_validation")
        self.assertNotIn("result_rows", result)

    def test_execution_sql_error_returns_refiner_state(self):
        class Warehouse:
            async def execute_sql(self, sql, timeout_seconds):
                raise OperationalErrorMock(1054, "Unknown column 'missing'")

        events = []
        runtime = SimpleNamespace(
            context={
                "dw_mysql_repository": Warehouse(),
                "feedback_learning_service": None,
            },
            stream_writer=events.append,
        )
        result = asyncio.run(execute_sql(self._state(), runtime))

        self.assertEqual(result["error_kind"], "sql_semantic")
        self.assertEqual(result["error_stage"], "execution")
        self.assertNotIn("result_rows", result)
        self.assertFalse(any(event.get("type") == "result" for event in events))

    def test_infrastructure_error_raises_and_never_returns_empty_success(self):
        class Warehouse:
            async def execute_sql(self, sql, timeout_seconds):
                raise ConnectionRefusedError("connection refused")

        events = []
        runtime = SimpleNamespace(
            context={
                "dw_mysql_repository": Warehouse(),
                "feedback_learning_service": None,
            },
            stream_writer=events.append,
        )
        with self.assertRaises(InfrastructureFailure):
            asyncio.run(execute_sql(self._state(), runtime))

        self.assertFalse(any(event.get("type") == "result" for event in events))


if __name__ == "__main__":
    unittest.main()
