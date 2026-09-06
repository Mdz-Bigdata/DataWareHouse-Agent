import asyncio
import unittest

from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.retrieval import batched_vector_search, merge_retrieval_results, recall_terms


class RetrievalTest(unittest.TestCase):
    def test_embedding_requests_are_bounded_to_backend_capacity(self):
        class EmbeddingClient:
            def __init__(self):
                self.batch_sizes = []

            async def aembed_documents(self, terms):
                self.batch_sizes.append(len(terms))
                return [[float(index)] for index, _ in enumerate(terms)]

        async def run():
            client = EmbeddingClient()

            async def search(_embedding):
                return []

            await batched_vector_search(
                terms=[f"term-{index}" for index in range(10)],
                embedding_client=client,
                search=search,
            )
            return client.batch_sizes

        self.assertEqual(asyncio.run(run()), [4, 4, 2])

    def test_original_query_and_canonical_metrics_are_never_truncated(self):
        question = "北京地区男性黄金会员的播放总次数且玄幻和言情类有声书的平均播放时长差多少"
        terms = recall_terms(
            ["播放", "玄幻", "黄金会员", "有声书", "言情", "时长"] * 3,
            {
                "metric_hints": ["播放总次数", "播放次数", "平均播放时长"],
                "filters": ["地区包含北京", "性别为男性"],
                "dimensions": ["地区", "会员", "分类"],
            },
            question,
        )

        self.assertEqual(terms[0], question)
        self.assertIn("播放次数", terms)
        self.assertIn("平均播放时长", terms)

    def test_hybrid_results_prefer_lexical_matches_and_deduplicate(self):
        class Item:
            def __init__(self, item_id):
                self.id = item_id

        lexical = [Item("play_count"), Item("average_played_seconds")]
        vector = [Item("ranking_play_count"), Item("play_count")]

        result = merge_retrieval_results(lexical, vector, limit=3)

        self.assertEqual([item.id for item in result], [
            "play_count",
            "average_played_seconds",
            "ranking_play_count",
        ])

    def test_parallel_recall_serializes_shared_meta_session_only(self):
        class GuardedMetaRepository:
            def __init__(self):
                self.active = 0
                self.max_active = 0

            async def _read(self):
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.active > 1:
                    raise RuntimeError("AsyncSession concurrent operation")
                try:
                    await asyncio.sleep(0)
                    return []
                finally:
                    self.active -= 1

            async def list_allowed_column_infos(self):
                return await self._read()

            async def list_metric_infos(self):
                return await self._read()

        class EmptyEmbeddingClient:
            async def aembed_documents(self, terms):
                return [[] for _ in terms]

        class EmptyVectorRepository:
            async def search(self, _embedding):
                return []

        class RuntimeStub:
            def __init__(self, context):
                self.context = context
                self.stream_writer = lambda _event: None

        async def run():
            meta_repository = GuardedMetaRepository()
            runtime = RuntimeStub(
                {
                    "meta_mysql_repository": meta_repository,
                    "meta_repository_lock": asyncio.Lock(),
                    "embedding_client": EmptyEmbeddingClient(),
                    "column_qdrant_repository": EmptyVectorRepository(),
                    "metric_qdrant_repository": EmptyVectorRepository(),
                }
            )
            state = {"query": "播放次数", "keywords": ["播放次数"]}
            await asyncio.gather(
                recall_column(state, runtime),
                recall_metric(state, runtime),
            )
            return meta_repository.max_active

        self.assertEqual(asyncio.run(run()), 1)
