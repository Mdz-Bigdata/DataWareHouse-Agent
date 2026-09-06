"""The explicitly selected demo warehouse never needs an external embedding API."""
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    from app.service.db_service import db_service
    from app.service.vector_service import VectorService


class DemoEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.vector = VectorService.__new__(VectorService)
        self.vector.embedding_dim = 1536

    def test_demo_does_not_read_credentials_or_call_external_service(self):
        with patch.object(db_service, "real_engine", None), patch.object(
            db_service, "active_db_type", "sqlite"
        ), patch("builtins.open", side_effect=AssertionError("must not read model configuration")), patch(
            "httpx.post", side_effect=AssertionError("must not call model service")
        ):
            first = self.vector.get_embedding("昨天听书各分类播放量是多少")
            second = self.vector.get_embedding("昨天听书各分类播放量是多少")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1536)

    def test_configured_database_keeps_online_embedding_path(self):
        config = {"active_vendor": "test", "vendors": {"test": {
            "api_key": "test-only", "base_url": "https://example.invalid/v1"
        }}}
        response = SimpleNamespace(status_code=200, json=lambda: {"data": [{"embedding": [1.0, 0.0]}]})
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=False), patch.object(db_service, "real_engine", object()), patch.object(
            db_service, "active_db_type", "postgres"
        ), patch("os.path.exists", return_value=True), patch(
            "builtins.open", return_value=io.StringIO(json.dumps(config))
        ), patch("httpx.post", return_value=response) as post:
            self.assertEqual(self.vector.get_embedding("test"), [1.0, 0.0])
        post.assert_called_once()

    def test_migrated_postgres_fixture_does_not_require_external_embeddings(self):
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=True), patch.object(
            db_service, "real_engine", object()
        ), patch("builtins.open", side_effect=AssertionError("must not read model configuration")), patch(
            "httpx.post", side_effect=AssertionError("must not call model service")
        ):
            self.assertEqual(len(self.vector.get_embedding("昨天听书播放量")), 1536)


if __name__ == "__main__":
    unittest.main()
