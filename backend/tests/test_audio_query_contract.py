"""HTTP regressions for audio queries, with isolated physical SQLite fixtures.

Each scenario runs in a fresh process because application imports initialize
database, semantic metadata, and cache singletons. Only external model/vector
calls and personal history are replaced; routing, SQL compilation, guardrails,
physical execution, cache reuse, and FastAPI response validation remain real.
"""

import importlib
import os
from pathlib import Path
import subprocess
import sys
from datetime import date, timedelta
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


QUESTION = "昨天听书各分类播放量是多少"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def run_scenario(missing_metric=False):
    """Exercise the production route without reading/writing personal history."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    memory_module = ModuleType("app.model.user_memory")
    memory_module.user_memory = Mock()
    memory_module.user_memory.get_preference_profile.return_value = {}
    vector_module = ModuleType("app.service.vector_service")
    vector_module.vector_service = Mock()
    vector_module.vector_service.get_embedding.return_value = [1.0, 0.0]
    vector_module.vector_service.recall_semantic_meta.return_value = []
    vector_module.vector_service.recall_fewshot_examples.return_value = []
    vector_module.vector_service.recall_error_corrections.return_value = []

    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        if missing_metric:
            connection.execute(text("CREATE TABLE unrelated_data (id INTEGER)"))
        else:
            connection.execute(text(
                "CREATE TABLE dws_audio_album_daily "
                "(dt DATE, category_name TEXT, play_count INTEGER)"
            ))
            connection.execute(text(
                "INSERT INTO dws_audio_album_daily VALUES (:dt, :category, :plays)"
            ), [
                {"dt": yesterday, "category": "回归甲", "plays": 13},
                {"dt": yesterday, "category": "回归甲", "plays": 8},
                {"dt": yesterday, "category": "回归乙", "plays": 34},
                {"dt": today.isoformat(), "category": "回归甲", "plays": 90001},
                {"dt": (today - timedelta(days=2)).isoformat(),
                 "category": "回归乙", "plays": 90002},
            ])

    raw_connection = engine.raw_connection()
    try:
        with patch.dict(os.environ, {"DB_TYPE": "sqlite", "MOCK_LLM": "true"}), patch.dict(
            sys.modules,
            {"app.model.user_memory": memory_module,
             "app.service.vector_service": vector_module},
        ):
            db_module = importlib.import_module("app.service.db_service")
            db = db_module.DBService.__new__(db_module.DBService)
            db.conn = raw_connection.driver_connection
            db.real_engine = engine
            db.active_db_type = "sqlite"
            db.execute_query = Mock(wraps=db.execute_query)
            with patch.object(db_module, "db_service", db):
                api = importlib.import_module("app.api.chat")
                schema = importlib.import_module("app.schema.chat")
                app = FastAPI()
                app.include_router(api.router, prefix="/api")
                with patch.object(
                    api.ask_agent, "_call_llm", side_effect=RuntimeError("offline regression")
                ), TestClient(app) as client:
                    request = {"question": QUESTION, "dialect": "doris",
                               "user": "anonymous", "role": "user"}
                    first = client.post("/api/chat/ask", json=request)
                    assert first.status_code == 200, first.text
                    response = first.json()
                    schema.AskResponse.model_validate(response)
                    if missing_metric:
                        assert response["success"] is False, response
                        assert "play_count" in response["error"], response
                        assert not response.get("data"), response
                        assert not response.get("chart"), response
                        assert response["cache_hit"] is False, response
                        db.execute_query.assert_not_called()
                    else:
                        assert response["success"] is True, response
                        assert response["cache_hit"] is False, response
                        assert isinstance(response["chart"]["config"], dict), response
                        assert "dws_audio_album_daily" in response["details"]["sql"], response
                        assert yesterday in response["details"]["sql"], response
                        assert any(
                            item.get("field") == "dt"
                            and item.get("value") == [yesterday, yesterday]
                            for item in response["details"]["filters"]
                        ), response
                        metric_columns = set(response["data"][0]) - {"category_name"}
                        assert len(metric_columns) == 1, response
                        metric_column = metric_columns.pop()
                        assert {row["category_name"]: row[metric_column]
                                for row in response["data"]} == {"回归甲": 21, "回归乙": 34}, response
                        db.execute_query.assert_called_once()

                    repeated = client.post("/api/chat/ask", json=request)
                    assert repeated.status_code == 200, repeated.text
                    second = repeated.json()
                    schema.AskResponse.model_validate(second)
                    if missing_metric:
                        assert second["success"] is False, second
                        assert not second.get("data"), second
                        assert second["cache_hit"] is False, second
                        db.execute_query.assert_not_called()
                    else:
                        assert second["success"] is True, second
                        assert second["cache_hit"] is True, second
                        assert second["cache_type"] == "exact", second
                        assert second["data"] == response["data"], second
                        assert second["chart"] == response["chart"], second
                        assert second["details"] == response["details"], second
                        db.execute_query.assert_called_once()
    finally:
        raw_connection.close()
        engine.dispose()


class AudioQueryContractTests(unittest.TestCase):
    def run_isolated(self, scenario):
        env = os.environ.copy()
        env.update({"DB_TYPE": "sqlite", "MOCK_LLM": "true", "PYTHONPATH": str(BACKEND_ROOT),
                    "PYTHONOPTIMIZE": "0"})
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--scenario", scenario],
            cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_audio_http_response_queries_yesterday_then_reuses_valid_cache(self):
        self.run_isolated("physical")

    def test_missing_audio_metric_returns_valid_failure_without_fabricated_rows(self):
        self.run_isolated("missing")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--scenario":
        run_scenario(missing_metric=sys.argv[2] == "missing")
    else:
        unittest.main()
