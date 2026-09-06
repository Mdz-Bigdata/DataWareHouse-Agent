from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.validate_sql import validate_sql
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.services.explain_budget_service import (
    ExplainBudgetError,
    ExplainEstimate,
    enforce_explain_budget,
    summarize_explain,
)


def test_mysql_and_postgres_explain_estimates_are_numeric_only():
    mysql = summarize_explain(
        [
            {"table": "play_session", "rows": 1000, "filtered": 10},
            {"table": "audio_album", "rows": 1, "filtered": 100},
        ],
        "mysql",
    )
    postgres = summarize_explain(
        [{"QUERY PLAN": "Seq Scan cost=0.00..431.00 rows=12000 width=8"}],
        "postgresql",
    )

    assert mysql.estimated_cost == 200
    assert mysql.estimated_rows == 1001
    assert postgres.estimated_cost == 431
    assert postgres.estimated_rows == 12000
    assert "Seq Scan" not in str(postgres.to_state())


def test_explain_budget_fails_closed_for_unknown_or_expensive_plans():
    with pytest.raises(ExplainBudgetError, match="未返回可验证"):
        enforce_explain_budget(
            ExplainEstimate(None, None, "unknown"), max_cost=100, max_rows=100
        )
    with pytest.raises(ExplainBudgetError, match="超过预算"):
        enforce_explain_budget(
            ExplainEstimate(101, 10, "mysql:rows"), max_cost=100, max_rows=100
        )


def _sql_state() -> dict:
    return {
        "sql": "SELECT id FROM audio_album",
        "table_infos": [
            {"name": "audio_album", "columns": [{"id": "audio_album.id", "name": "id"}]}
        ],
        "relationships": [],
        "row_level_scope": [],
        "access_policy": {},
        "analysis_plan": {},
        "db_info": {"dialect": "mysql"},
        "correction_attempts": 0,
    }


def test_sql_nodes_expose_enforced_pipeline_order():
    class Warehouse:
        async def validate_sql(self, _sql, _timeout_seconds):
            return ExplainEstimate(12, 10, "mysql:rows")

        async def execute_sql(self, _sql, _timeout_seconds):
            return [{"id": 1}]

    events: list[dict] = []
    runtime = SimpleNamespace(
        context={"dw_mysql_repository": Warehouse(), "feedback_learning_service": None},
        stream_writer=events.append,
    )
    state = _sql_state()
    state.update(asyncio.run(validate_sql(state, runtime)))
    state.update(asyncio.run(execute_sql(state, runtime)))

    assert state["sql_validation_stages"] == [
        "ast_permissions",
        "rls_injection",
        "post_rls_ast",
        "explain_cost",
        "read_only_timeout",
    ]
    assert state["execution_mode"] == "read_only"
    assert state["result_rows"] == [{"id": 1}]


def test_repository_executes_in_read_only_transaction_and_rolls_back():
    calls: list[str] = []

    class MappingResult:
        def mappings(self):
            return self

        def fetchall(self):
            return [{"id": 1}]

    class Session:
        def in_transaction(self):
            return True

        async def rollback(self):
            calls.append("rollback")

        async def execute(self, statement):
            calls.append(str(statement))
            return MappingResult()

    class Dialect:
        name = "mysql"

        async def apply_read_only(self, _session):
            calls.append("read_only")

        async def reset_read_only(self, _session):
            calls.append("read_write")

        async def apply_execution_timeout(self, _session, _seconds):
            calls.append("timeout")

        async def reset_execution_timeout(self, _session):
            calls.append("reset_timeout")

    repository = DWMySQlRepository.__new__(DWMySQlRepository)
    repository.session = Session()
    repository.dialect = Dialect()

    rows = asyncio.run(repository.execute_sql("SELECT id FROM audio_album", 3))

    assert rows == [{"id": 1}]
    assert calls == [
        "rollback",
        "read_only",
        "timeout",
        "SELECT id FROM audio_album",
        "reset_timeout",
        "rollback",
        "read_write",
        "rollback",
    ]
