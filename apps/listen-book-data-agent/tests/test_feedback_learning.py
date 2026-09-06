"""Phase 2.1：Few-shot 自愈学习闭环测试。

用 Fake repository + Fake embedding client 测试 FeedbackLearningService 的
召回与回写逻辑，不依赖真实 Qdrant/Embedding 服务。
"""

import asyncio
import unittest

from app.entities.feedback_entry import FeedbackEntry
from app.services.feedback_learning_service import FeedbackLearningService


class FakeFeedbackRepository:
    """内存版 feedback repository 桩。"""

    def __init__(self):
        self.stored: dict[str, tuple[FeedbackEntry, list[float]]] = {}
        self.search_should_fail = False

    async def ensure_collection(self):
        pass

    async def upsert(self, entry: FeedbackEntry, embedding: list[float]):
        self.stored[entry.id] = (entry, embedding)

    async def search(self, embedding, score_threshold=0.7, limit=3, lifecycle="published"):
        if self.search_should_fail:
            raise RuntimeError("search failed")
        # 简单返回所有存储的经验（测试不关心向量相似度，只验证召回链路）
        return [entry for entry, _ in self.stored.values() if entry.lifecycle == lifecycle][:limit]


class FakeEmbeddingClient:
    """固定向量的 embedding 桩。"""

    async def aembed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FeedbackLearningServiceTest(unittest.TestCase):
    def setUp(self):
        self.repo = FakeFeedbackRepository()
        self.embedding = FakeEmbeddingClient()
        self.service = FeedbackLearningService(self.repo, self.embedding)

    def test_record_success_fix_stores_entry(self):
        asyncio.run(
            self.service.record_success_fix(
                question="统计播放次数",
                error_sql="SELECT count FROM play_session",
                corrected_sql="SELECT COUNT(*) FROM play_session",
                error_message="Unknown column 'count'",
                table_signature="play_session",
            )
        )
        self.assertEqual(len(self.repo.stored), 1)
        entry = next(iter(self.repo.stored.values()))[0]
        self.assertEqual(entry.lifecycle, "candidate")
        self.assertEqual(entry.source, "auto_repair")

    def test_record_skip_when_sql_unchanged(self):
        # error_sql == corrected_sql 时不回写（没有学习价值）
        asyncio.run(
            self.service.record_success_fix(
                question="测试",
                error_sql="SELECT 1",
                corrected_sql="SELECT 1",
                error_message="",
                table_signature="",
            )
        )
        self.assertEqual(len(self.repo.stored), 0)

    def test_record_skip_when_empty_input(self):
        asyncio.run(
            self.service.record_success_fix(
                question="",
                error_sql="SELECT 1",
                corrected_sql="SELECT 2",
                error_message="",
                table_signature="",
            )
        )
        self.assertEqual(len(self.repo.stored), 0)

    def test_record_silently_fails_on_exception(self):
        # embedding 失败不应抛异常（best-effort）
        bad_service = FeedbackLearningService(self.repo, _FailingEmbeddingClient())
        asyncio.run(
            bad_service.record_success_fix(
                question="测试",
                error_sql="SELECT 1",
                corrected_sql="SELECT 2",
                error_message="",
                table_signature="",
            )
        )
        self.assertEqual(len(self.repo.stored), 0)

    def test_auto_repair_candidate_is_never_recalled(self):
        asyncio.run(
            self.service.record_success_fix(
                question="统计播放次数",
                error_sql="SELECT count FROM play_session",
                corrected_sql="SELECT COUNT(*) FROM play_session",
                error_message="Unknown column",
                table_signature="play_session",
            )
        )
        results = asyncio.run(self.service.recall_similar_fixes("播放次数统计"))
        self.assertEqual(results, [])

    def test_recall_returns_only_published_entries(self):
        published = FeedbackEntry(
            id="published-1",
            question="统计播放次数",
            error_sql="SELECT count FROM play_session",
            corrected_sql="SELECT COUNT(*) FROM play_session",
            error_message="Unknown column",
            table_signature="play_session",
            lifecycle="published",
            source="reviewed",
        )
        self.repo.stored[published.id] = (published, [0.1, 0.2, 0.3])

        results = asyncio.run(self.service.recall_similar_fixes("播放次数统计"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].question, "统计播放次数")

    def test_candidate_sql_is_pre_rls_parameterized_and_text_is_redacted(self):
        asyncio.run(
            self.service.record_success_fix(
                question="查询手机号 13800138000 在华东的订单",
                error_sql=(
                    "SELECT o.missing FROM orders o WHERE o.status = 'paid' "
                    "AND o.region = '华东' LIMIT 500"
                ),
                corrected_sql=(
                    "SELECT id FROM orders o WHERE o.status = 'completed' "
                    "AND o.region = '华东' LIMIT 500"
                ),
                error_message="手机号 13800138000 的状态 'paid' 无效",
                table_signature="orders",
                row_level_scope=[
                    {
                        "table": "orders",
                        "column": "region",
                        "value": "华东",
                    }
                ],
            )
        )

        entry = next(iter(self.repo.stored.values()))[0]
        self.assertNotIn("13800138000", entry.question)
        self.assertNotIn("13800138000", entry.error_message)
        self.assertNotIn("华东", entry.corrected_sql)
        self.assertNotIn("region", entry.corrected_sql)
        self.assertNotIn("completed", entry.corrected_sql)
        self.assertIn(":p1", entry.corrected_sql)
        self.assertEqual(entry.corrected_parameter_types, ("string",))

    def test_recall_returns_empty_for_empty_question(self):
        results = asyncio.run(self.service.recall_similar_fixes(""))
        self.assertEqual(results, [])

    def test_recall_silently_fails_on_exception(self):
        self.repo.search_should_fail = True
        results = asyncio.run(self.service.recall_similar_fixes("测试"))
        self.assertEqual(results, [])

    def test_record_updates_same_question(self):
        # 同一 question 的 sha256 相同，应覆盖而非堆积
        asyncio.run(
            self.service.record_success_fix(
                question="重复问题",
                error_sql="SELECT a",
                corrected_sql="SELECT b",
                error_message="err1",
                table_signature="t1",
            )
        )
        asyncio.run(
            self.service.record_success_fix(
                question="重复问题",
                error_sql="SELECT c",
                corrected_sql="SELECT d",
                error_message="err2",
                table_signature="t2",
            )
        )
        self.assertEqual(len(self.repo.stored), 1)


class _FailingEmbeddingClient:
    async def aembed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedding unavailable")


if __name__ == "__main__":
    unittest.main()
