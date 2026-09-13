"""旁路取数通道必须收口，删除全部纠错经验必须可确认、可还原。

覆盖两条安全红线：

1. `/chat/metadata/enrich` 背后的 Profiling 采样曾经用 f-string 直接把外部
   table_name 拼进 `SELECT * FROM {table_name}` 并直连 db_service，绕开了
   仓库里全部 SQL 准入网闸。现在任何表名都必须先过标识符校验 + 已注册表
   白名单，并从统一的 guardrail 出口执行；被拒绝时**一条 SQL 都不能发出去**。
2. `DELETE /chat/corrections/clear` 曾经无确认、无备份地清空全部纠错经验。
   现在必须显式 confirm=true，且删除前先落回收站归档，误删可以还原。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# 与 tests/test_demo_workflows.py 同一约定：导入应用前固定演示数据源，
# 保证这组安全回归永远不会连到真实业务库上去。
os.environ["DB_TYPE"] = "sqlite"

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.model.user_memory import UserMemory
from app.service.guardrail import GuardrailException, guardrail
from app.service.metadata_enricher import (
    MAX_SAMPLE_SIZE, MetadataEnricher, UnsafeSampleSizeError, UnsafeTableNameError,
)

# 真实仓库里常见的注入载荷。它们全都必须在「发出 SQL 之前」被拒绝，
# 而不是被转义、清洗后照样执行。
INJECTION_PAYLOADS = [
    "x; DROP TABLE y",
    "articles; DELETE FROM articles",
    "articles UNION SELECT password FROM pg_shadow",
    "articles WHERE 1=1",
    "articles--",
    "articles/*comment*/",
    "(SELECT 1)",
    "'articles'",
    '"articles"',
    "`articles`",
    "art icles",
    "articles\n; DROP TABLE y",
    "1articles",
    "-articles",
]


class _FakeCursor:
    def __init__(self, tables, views):
        self._tables, self._views, self._rows = tables, views, []

    def execute(self, sql, *args):
        self._rows = [(name,) for name in (self._views if "'view'" in sql else self._tables)]

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, tables, views):
        self._tables, self._views = tables, views

    def cursor(self):
        return _FakeCursor(self._tables, self._views)


class FakeDBService:
    """替身数据源：记录所有真正发出的 SQL，绝不连真实数据库。"""

    def __init__(self, tables=("articles", "dws_trade_order_daily"), views=(), schemas=()):
        self.real_engine = None
        self.conn = _FakeConn(list(tables), list(views))
        self.query_schemas = list(schemas)
        self.executed = []

    def execute_query(self, sql, dialect="mysql"):
        self.executed.append(sql)
        return pd.DataFrame({"id": [1, 2], "gmv": [10.0, 20.0]})


class TableNameChokepointTests(unittest.TestCase):
    """#4 旁路取数通道：表名收口。"""

    def setUp(self):
        self.enricher = MetadataEnricher()
        self.db = FakeDBService()
        patcher = patch("app.service.metadata_enricher.db_service", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        # guardrail 的物理 EXPLAIN 分支会去连真实库，这里固定走演示分支。
        env = patch.dict(os.environ, {"DB_TYPE": "sqlite"})
        env.start()
        self.addCleanup(env.stop)

    def test_injection_payloads_are_rejected_before_any_sql_is_issued(self):
        for payload in INJECTION_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(UnsafeTableNameError):
                    self.enricher.profile_table(payload)
        self.assertEqual(self.db.executed, [], "被拒绝的表名不得产生任何一条 SQL")

    def test_unregistered_table_cannot_be_read(self):
        """越权读取：语法完全合法、但不在白名单里的表必须拒绝。"""
        for name in ("pg_shadow", "users", "secret_salaries", "sqlite_master"):
            with self.subTest(name=name):
                with self.assertRaises(UnsafeTableNameError):
                    self.enricher.profile_table(name)
        self.assertEqual(self.db.executed, [])

    def test_non_string_table_names_are_rejected(self):
        for value in (None, 5, ["articles"], {"t": "articles"}, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(UnsafeTableNameError):
                    self.enricher.profile_table(value)
        self.assertEqual(self.db.executed, [])

    def test_registered_table_is_sampled_with_a_quoted_identifier(self):
        with patch.object(guardrail, "check_sql", wraps=guardrail.check_sql) as gate:
            profile = self.enricher.profile_table("articles", sample_size=5)

        self.assertEqual(self.db.executed, ["SELECT * FROM `articles` LIMIT 5"])
        self.assertEqual(profile["table_name"], "articles")
        self.assertEqual(profile["total_sampled_rows"], 2)
        # 必须走统一安全出口，而不是直连 db_service。
        gate.assert_called_once()
        self.assertEqual(gate.call_args.args[0], "SELECT * FROM `articles` LIMIT 5")

    def test_case_variants_resolve_to_the_registered_spelling(self):
        self.enricher.profile_table("ARTICLES", sample_size=3)
        self.assertEqual(self.db.executed, ["SELECT * FROM `articles` LIMIT 3"])

    def test_surrounding_whitespace_is_tolerated_but_inner_whitespace_is_not(self):
        """首尾空格是常见的复制粘贴噪声，允许；名字内部的空格一律当作注入。"""
        self.enricher.profile_table("  articles  ", sample_size=3)
        self.assertEqual(self.db.executed, ["SELECT * FROM `articles` LIMIT 3"])
        with self.assertRaises(UnsafeTableNameError):
            self.enricher.profile_table("articles x")

    def test_schema_qualified_names_need_a_known_schema(self):
        self.db.query_schemas = ["public"]
        self.enricher.profile_table("public.articles", sample_size=1)
        self.assertEqual(self.db.executed, ['SELECT * FROM `public`.`articles` LIMIT 1'])

        for name in ("evil.articles", "information_schema.articles", "pg_catalog.articles"):
            with self.subTest(name=name):
                with self.assertRaises(UnsafeTableNameError):
                    self.enricher.profile_table(name)
        self.assertEqual(len(self.db.executed), 1, "未知 schema 不得发出 SQL")

    def test_nested_qualification_is_rejected(self):
        with self.assertRaises(UnsafeTableNameError):
            self.enricher.profile_table("db.public.articles")
        self.assertEqual(self.db.executed, [])

    def test_views_stay_profilable_but_still_go_through_the_whitelist(self):
        self.db.conn = _FakeConn(["articles"], ["v_article_daily"])
        self.enricher.profile_table("v_article_daily", sample_size=2)
        self.assertEqual(self.db.executed, ["SELECT * FROM `v_article_daily` LIMIT 2"])

    def test_sample_size_must_be_a_bounded_integer(self):
        for bad in ("5; DROP TABLE articles", "1 OR 1=1", None, 0, -3, "abc"):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafeSampleSizeError):
                    self.enricher.profile_table("articles", sample_size=bad)
        self.assertEqual(self.db.executed, [])

        self.enricher.profile_table("articles", sample_size=10 ** 9)
        self.assertEqual(self.db.executed, [f"SELECT * FROM `articles` LIMIT {MAX_SAMPLE_SIZE}"])

    def test_guardrail_rejection_returns_no_rows(self):
        with patch.object(guardrail, "check_sql", side_effect=GuardrailException("测试拦截")):
            profile = self.enricher.profile_table("articles")
        self.assertEqual(self.db.executed, [], "网闸拦截后不得再执行查询")
        self.assertEqual(profile["columns"], [])
        self.assertIn("安全网闸拦截", profile["error"])

    def test_enrich_rejects_unregistered_table_even_with_a_supplied_profile(self):
        """自带 profile 时不触发采样，但产出的口径会写进语义层，同样不能放行。"""
        handcrafted = {"columns": [{"column_name": "gmv", "dtype": "float64",
                                    "distinct_count": 3, "sample_values": [1, 2, 3]}]}
        with self.assertRaises(UnsafeTableNameError):
            self.enricher.enrich_metadata("evil_table", table_profile=handcrafted)
        with self.assertRaises(UnsafeTableNameError):
            self.enricher.enrich_metadata("articles; DROP TABLE x", table_profile=handcrafted)
        self.assertEqual(self.db.executed, [])

    def test_enriched_calculation_only_references_the_registered_table(self):
        handcrafted = {"columns": [{"column_name": "gmv", "dtype": "float64",
                                    "distinct_count": 3, "sample_values": [1, 2, 3]}]}
        enriched = self.enricher.enrich_metadata("ARTICLES", table_profile=handcrafted)
        self.assertEqual(enriched["table_name"], "articles")
        self.assertEqual(enriched["metrics"][0]["calculation"], "SUM(articles.gmv)")


class MetadataEnrichEndpointTests(unittest.TestCase):
    """API 层：非法表名以 400 拒绝，不再泄漏成 500 或直接执行。"""

    def setUp(self):
        self.db = FakeDBService()
        patcher = patch("app.service.metadata_enricher.db_service", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"DB_TYPE": "sqlite"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_injection_payload_is_refused_with_400(self):
        for payload in ("x; DROP TABLE y", "articles UNION SELECT 1", "pg_shadow"):
            with self.subTest(payload=payload):
                response = self.client.post("/api/chat/metadata/enrich",
                                            params={"table_name": payload})
                self.assertEqual(response.status_code, 400)
                self.assertIn("安全拦截", response.json()["detail"])
        self.assertEqual(self.db.executed, [])

    def test_registered_table_still_enriches_normally(self):
        response = self.client.post("/api/chat/metadata/enrich",
                                    params={"table_name": "articles"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["table_name"], "articles")
        self.assertEqual(self.db.executed, ["SELECT * FROM `articles` LIMIT 1000"])


class CorrectionDeleteProtectionTests(unittest.TestCase):
    """纠错经验的删除保护：二次确认 + 软删 + 可还原。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "user_memory.json")
        try:
            self.memory = UserMemory(storage_path=path, seed_demo=False)
        except TypeError:  # 兼容尚未引入 seed_demo 开关的版本
            self.memory = UserMemory(storage_path=path)
        self.memory.error_corrections = []

        memory_patch = patch("app.api.chat.user_memory", self.memory)
        memory_patch.start()
        self.addCleanup(memory_patch.stop)
        self.vector = MagicMock()
        vector_patch = patch("app.service.vector_service.vector_service", self.vector)
        vector_patch.start()
        self.addCleanup(vector_patch.stop)

        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for i in range(2):
            self.memory.add_error_correction(
                question=f"第{i}个问题", error_message=f"err{i}",
                wrong_sql=f"SELECT {i}", corrected_sql=f"SELECT {i} FROM articles")

    @property
    def archive_path(self):
        from app.api.chat import _correction_archive_path
        return _correction_archive_path()

    def questions(self):
        return [r["question"] for r in self.memory.get_error_corrections()]

    def test_clear_without_confirmation_changes_nothing(self):
        response = self.client.delete("/api/chat/corrections/clear")
        self.assertEqual(response.status_code, 400)
        self.assertIn("confirm=true", response.json()["detail"])
        self.assertEqual(len(self.memory.get_error_corrections()), 2)
        self.assertFalse(os.path.exists(self.archive_path))
        self.vector.ingest_error_corrections.assert_not_called()

    def test_confirmed_clear_is_a_soft_delete_that_can_be_restored(self):
        before = list(self.memory.get_error_corrections())
        response = self.client.delete("/api/chat/corrections/clear",
                                      params={"confirm": "true", "reason": "误操作演练"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["archived_count"], 2)
        self.assertEqual(self.memory.get_error_corrections(), [])

        with open(self.archive_path, encoding="utf-8") as f:
            archived = json.load(f)["batches"]
        self.assertEqual(archived[-1]["records"], before)
        self.assertEqual(archived[-1]["reason"], "误操作演练")

        restored = self.client.post("/api/chat/corrections/restore",
                                    params={"batch_id": body["batch_id"]})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["restored_count"], 2)
        self.assertEqual(self.memory.get_error_corrections(), before)

    def test_restore_without_batch_id_recovers_the_latest_deletion_and_is_idempotent(self):
        before = list(self.memory.get_error_corrections())
        self.client.delete("/api/chat/corrections/clear", params={"confirm": "true"})
        first = self.client.post("/api/chat/corrections/restore")
        second = self.client.post("/api/chat/corrections/restore")
        self.assertEqual(first.json()["restored_count"], 2)
        self.assertEqual(second.json()["restored_count"], 0, "重复还原不得产生重复记录")
        self.assertEqual(self.memory.get_error_corrections(), before)

    def test_restored_corrections_survive_a_reload_from_disk(self):
        self.client.delete("/api/chat/corrections/clear", params={"confirm": "true"})
        self.client.post("/api/chat/corrections/restore")
        try:
            reloaded = UserMemory(storage_path=self.memory.storage_path, seed_demo=False)
        except TypeError:
            reloaded = UserMemory(storage_path=self.memory.storage_path)
        self.assertEqual([r["question"] for r in reloaded.get_error_corrections()],
                         self.questions())

    def test_single_delete_is_recoverable(self):
        response = self.client.delete("/api/chat/corrections/delete",
                                      params={"question": "第0个问题"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.questions(), ["第1个问题"])

        restored = self.client.post("/api/chat/corrections/restore",
                                    params={"batch_id": response.json()["batch_id"]})
        self.assertEqual(restored.status_code, 200)
        self.assertIn("第0个问题", self.questions())

    def test_deleting_an_unknown_question_still_returns_404_without_archiving(self):
        response = self.client.delete("/api/chat/corrections/delete",
                                      params={"question": "不存在的问题"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(os.path.exists(self.archive_path))

    def test_clear_is_abandoned_when_the_recycle_bin_cannot_be_written(self):
        """归档写不进去就不许删 —— 宁可删不掉，也不能删了找不回来。"""
        with patch("app.api.chat._write_correction_archive", side_effect=OSError("磁盘只读")):
            response = self.client.delete("/api/chat/corrections/clear",
                                          params={"confirm": "true"})
        self.assertEqual(response.status_code, 500)
        self.assertIn("已放弃清空", response.json()["detail"])
        self.assertEqual(len(self.memory.get_error_corrections()), 2)
        self.vector.ingest_error_corrections.assert_not_called()

    def test_archive_endpoint_reports_every_deletion_batch(self):
        self.client.delete("/api/chat/corrections/delete", params={"question": "第0个问题"})
        self.client.delete("/api/chat/corrections/clear", params={"confirm": "true"})
        body = self.client.get("/api/chat/corrections/archive").json()
        self.assertEqual(body["batch_count"], 2)
        self.assertEqual([b["count"] for b in body["batches"]], [1, 1])
        self.assertEqual(body["archive_path"], self.archive_path)

    def test_clearing_an_empty_set_stays_a_no_op(self):
        self.memory.error_corrections = []
        response = self.client.delete("/api/chat/corrections/clear", params={"confirm": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["archived_count"], 0)
        self.assertFalse(os.path.exists(self.archive_path))


if __name__ == "__main__":
    unittest.main()
