# -*- coding: utf-8 -*-
"""派生向量索引治理：幂等点 ID、预过滤、出处字段、降级隔离、增量重嵌。

这些测试守住的是一条：派生索引里的相似度分数必须是**可比**的。
点 ID 不幂等会留下重复/孤儿点；没有预过滤会把 ada-002 向量和本地哈希向量
排在同一张榜上；payload 没有出处就无法事后判断哪些分数不可信。
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.models import Distance, PointStruct, VectorParams

with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    from app.service.db_service import db_service
    from app.service.vector_service import (
        GLOBAL_SOURCE_ID,
        HASH_EMBEDDING_MODEL,
        ONLINE_EMBEDDING_MODEL,
        STATUS_ACTIVE,
        STATUS_DEGRADED,
        VectorService,
        content_fingerprint,
        derive_point_id,
        vector_service,
    )

TEST_DIM = 16


def fake_metric(name, table, aliases=(), description=""):
    return SimpleNamespace(name=name, source_table=table, aliases=list(aliases),
                           description=description, default_agg="SUM")


def fake_dimension(name, table, aliases=(), value_range=()):
    return SimpleNamespace(name=name, source_table=table, aliases=list(aliases),
                           value_range=list(value_range))


def fake_layer(metrics=(), dimensions=()):
    return SimpleNamespace(metrics={m.name: m for m in metrics},
                           dimensions={d.name: d for d in dimensions})


def build_service(source_id="src-a"):
    """一个隔离的 VectorService：自己的内存 Qdrant，不碰全局单例。"""
    svc = VectorService.__new__(VectorService)
    svc.client = QdrantClient(location=":memory:")
    svc.embedding_dim = TEST_DIM
    svc.metrics_collection = "dwh_metrics"
    svc.dims_collection = "dwh_dims"
    svc.value_collection = "value_indices"
    svc.example_collection = "few_shots"
    svc.error_correction_collection = "few_shots_corrections"
    svc.enable_prefilter = True
    svc.exclude_degraded_on_recall = False
    svc.last_embedding_model = HASH_EMBEDDING_MODEL
    svc.last_embedding_status = STATUS_ACTIVE
    svc.last_degraded_reason = ""
    import threading
    svc._write_lock = threading.RLock()
    svc._init_collections()
    svc.active_source_id = lambda: source_id
    return svc


def scroll_all(svc, collection):
    out, offset = [], None
    while True:
        batch, offset = svc.client.scroll(collection_name=collection, limit=256,
                                          offset=offset, with_payload=True, with_vectors=False)
        out.extend(batch)
        if offset is None:
            break
    return out


def point_ids(svc, collection):
    return {str(record.id) for record in scroll_all(svc, collection)}


class DeterministicPointIdTests(unittest.TestCase):
    """自增 ID 会让同一个业务对象在重跑后落到不同的点上。"""

    def test_id_is_derived_from_business_key_and_stable(self):
        first = derive_point_id("dwh_metrics", "src-a", "metric::t::gmv")
        second = derive_point_id("dwh_metrics", "src-a", "metric::t::gmv")
        self.assertEqual(first, second)

    def test_id_separates_collection_source_and_business_key(self):
        base = derive_point_id("dwh_metrics", "src-a", "metric::t::gmv")
        self.assertNotEqual(base, derive_point_id("dwh_dims", "src-a", "metric::t::gmv"))
        self.assertNotEqual(base, derive_point_id("dwh_metrics", "src-b", "metric::t::gmv"))
        self.assertNotEqual(base, derive_point_id("dwh_metrics", "src-a", "metric::t::orders"))

    def test_fingerprint_tracks_content_change(self):
        self.assertEqual(content_fingerprint("abc"), content_fingerprint("abc"))
        self.assertNotEqual(content_fingerprint("abc"), content_fingerprint("abd"))


class IdempotentIngestTests(unittest.TestCase):
    """重复灌库不得产生重复点，删掉的业务对象不得留下孤儿点。"""

    def setUp(self):
        self.svc = build_service()
        self.layer = fake_layer(
            metrics=[fake_metric("gmv", "fact_order", ["销售额"], "成交额"),
                     fake_metric("order_count", "fact_order", ["订单数"], "单量")],
            dimensions=[fake_dimension("region_name", "dim_region", ["大区"], ["华东", "华北"])],
        )
        patcher = patch("app.service.vector_service.semantic_layer", self.layer)
        patcher.start()
        self.addCleanup(patcher.stop)
        # ingest_metadata 末尾会重建全局 BM25 索引，测试里不该污染它。
        bm25 = patch("app.service.hybrid_retriever.hybrid_retriever", MagicMock())
        bm25.start()
        self.addCleanup(bm25.stop)

    def test_repeated_ingest_does_not_duplicate_points(self):
        self.svc.ingest_metadata()
        first = {c: point_ids(self.svc, c) for c in
                 (self.svc.metrics_collection, self.svc.dims_collection, self.svc.value_collection)}
        for _ in range(3):
            self.svc.ingest_metadata()
        second = {c: point_ids(self.svc, c) for c in
                  (self.svc.metrics_collection, self.svc.dims_collection, self.svc.value_collection)}
        self.assertEqual(first, second)
        self.assertEqual(len(first[self.svc.metrics_collection]), 2)
        self.assertEqual(len(first[self.svc.value_collection]), 2)

    def test_unchanged_points_are_not_re_embedded(self):
        self.svc.ingest_metadata()
        with patch.object(self.svc, "_embed_for_index",
                          side_effect=AssertionError("unchanged point must not be re-embedded")):
            self.svc.ingest_metadata()

    def test_removed_metric_is_pruned_not_orphaned(self):
        self.svc.ingest_metadata()
        self.assertEqual(len(point_ids(self.svc, self.svc.metrics_collection)), 2)
        del self.layer.metrics["order_count"]
        self.svc.ingest_metadata()
        remaining = [r.payload["metric_name"] for r in scroll_all(self.svc, self.svc.metrics_collection)]
        self.assertEqual(remaining, ["gmv"])

    def test_other_data_source_points_survive_pruning(self):
        self.svc.ingest_metadata()
        self.svc.active_source_id = lambda: "src-b"
        self.svc.ingest_metadata()
        sources = {r.payload["source_id"] for r in scroll_all(self.svc, self.svc.metrics_collection)}
        self.assertEqual(sources, {"src-a", "src-b"})

    def test_same_metric_name_on_two_tables_keeps_two_points(self):
        self.layer.metrics["gmv_b"] = fake_metric("gmv", "fact_refund", ["退款额"], "另一张表的同名指标")
        self.svc.ingest_metadata()
        tables = {r.payload["table_name"] for r in scroll_all(self.svc, self.svc.metrics_collection)}
        self.assertEqual(tables, {"fact_order", "fact_refund"})


class ProvenancePayloadTests(unittest.TestCase):
    """每个点都要能回答：谁、哪个数据源、哪个模型、什么状态。"""

    def setUp(self):
        self.svc = build_service()
        self.layer = fake_layer(
            metrics=[fake_metric("gmv", "fact_order", ["销售额"], "成交额")],
            dimensions=[fake_dimension("region_name", "dim_region", ["大区"], ["华东"])],
        )
        patcher = patch("app.service.vector_service.semantic_layer", self.layer)
        patcher.start()
        self.addCleanup(patcher.stop)
        bm25 = patch("app.service.hybrid_retriever.hybrid_retriever", MagicMock())
        bm25.start()
        self.addCleanup(bm25.stop)

    def test_every_indexed_point_carries_provenance(self):
        self.svc.ingest_metadata()
        self.svc.ingest_fewshot_examples()
        expected_kind = {
            self.svc.metrics_collection: "metric",
            self.svc.dims_collection: "dimension",
            self.svc.value_collection: "value",
            self.svc.example_collection: "fewshot",
        }
        for collection, kind in expected_kind.items():
            records = scroll_all(self.svc, collection)
            self.assertTrue(records, collection)
            for record in records:
                payload = record.payload
                for key in ("source_id", "embedding_model", "status", "content_hash", "indexed_at"):
                    self.assertIn(key, payload, f"{collection} missing {key}")
                self.assertEqual(payload["point_kind"], kind)
                self.assertIn(payload["status"], (STATUS_ACTIVE, STATUS_DEGRADED))

    def test_metadata_points_are_scoped_to_the_active_source(self):
        self.svc.ingest_metadata()
        sources = {r.payload["source_id"] for r in scroll_all(self.svc, self.svc.metrics_collection)}
        self.assertEqual(sources, {"src-a"})

    def test_memory_backed_points_use_the_global_scope(self):
        """Few-shot 与纠错经验不随数据源切换，绑到具体数据源会在切换后被误杀。"""
        self.svc.ingest_fewshot_examples()
        sources = {r.payload["source_id"] for r in scroll_all(self.svc, self.svc.example_collection)}
        self.assertEqual(sources, {GLOBAL_SOURCE_ID})

    def test_describe_index_reports_model_and_status_mix(self):
        self.svc.ingest_metadata()
        report = self.svc.describe_index()
        stats = report["collections"][self.svc.metrics_collection]
        self.assertFalse(stats["mixed_embedding_models"])
        self.assertEqual(stats["degraded_points"], 0)
        self.assertEqual(stats["points"], 1)


class DegradationProvenanceTests(unittest.TestCase):
    """★ 最严重的一条：降级必须留痕，否则哈希向量与 ada-002 向量混存且无从分辨。"""

    def setUp(self):
        self.svc = build_service()

    def test_online_failure_is_recorded_as_degraded_hash_embedding(self):
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=False), \
             patch.object(VectorService, "_read_embedding_vendor",
                          return_value=("k", "https://example.invalid/v1")), \
             patch("httpx.post", side_effect=RuntimeError("network down")):
            vector, model, status = self.svc.embed_with_provenance("华东区退款额")
        self.assertEqual(len(vector), TEST_DIM)
        self.assertEqual(model, HASH_EMBEDDING_MODEL)
        self.assertEqual(status, STATUS_DEGRADED)
        self.assertIn("network down", self.svc.last_degraded_reason)

    def test_online_success_is_recorded_as_active_online_embedding(self):
        response = SimpleNamespace(status_code=200,
                                   json=lambda: {"data": [{"embedding": [0.5] * TEST_DIM}]})
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=False), \
             patch.object(VectorService, "_read_embedding_vendor",
                          return_value=("k", "https://example.invalid/v1")), \
             patch("httpx.post", return_value=response):
            _vector, model, status = self.svc.embed_with_provenance("华东区退款额")
        self.assertEqual(model, ONLINE_EMBEDDING_MODEL)
        self.assertEqual(status, STATUS_ACTIVE)

    def test_hash_is_not_labelled_degraded_when_no_online_model_configured(self):
        """没配在线模型时哈希就是既定路径，把它标成 degraded 会让告警失去意义。"""
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=False), \
             patch.object(VectorService, "_read_embedding_vendor", return_value=("", "")):
            _vector, model, status = self.svc.embed_with_provenance("华东区退款额")
        self.assertEqual(model, HASH_EMBEDDING_MODEL)
        self.assertEqual(status, STATUS_ACTIVE)

    def test_get_embedding_still_returns_a_bare_vector(self):
        """既有调用方（语义缓存等）拿到的仍是纯向量，契约不变。"""
        result = self.svc.get_embedding("华东区退款额")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), TEST_DIM)

    def test_wrong_dimension_vector_degrades_instead_of_breaking_ingest(self):
        """在线模型换代成 3072 维时，灌库不该整条炸掉。"""
        response = SimpleNamespace(status_code=200,
                                   json=lambda: {"data": [{"embedding": [0.5] * (TEST_DIM * 2)}]})
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=False), \
             patch.object(VectorService, "_read_embedding_vendor",
                          return_value=("k", "https://example.invalid/v1")), \
             patch("httpx.post", return_value=response):
            vector, model, status = self.svc._embed_for_index("华东区退款额")
        self.assertEqual(len(vector), TEST_DIM)
        self.assertEqual(model, HASH_EMBEDDING_MODEL)
        self.assertEqual(status, STATUS_DEGRADED)

    def test_degraded_point_is_labelled_in_its_payload(self):
        item = {"question": "q1", "error_message": "e1", "wrong_sql": "s1", "corrected_sql": "s2"}
        with patch.object(type(db_service), "is_sample_data", new_callable=PropertyMock, return_value=False), \
             patch.object(VectorService, "_read_embedding_vendor",
                          return_value=("k", "https://example.invalid/v1")), \
             patch("httpx.post", side_effect=RuntimeError("boom")):
            self.svc.upsert_error_correction(item)
        record = scroll_all(self.svc, self.svc.error_correction_collection)[0]
        self.assertEqual(record.payload["status"], STATUS_DEGRADED)
        self.assertEqual(record.payload["embedding_model"], HASH_EMBEDDING_MODEL)


class PreFilterTests(unittest.TestCase):
    """五处检索此前全裸奔，没有任何 query_filter。"""

    def setUp(self):
        self.svc = build_service()
        self.layer = fake_layer(
            metrics=[fake_metric("gmv", "fact_order", ["销售额"], "成交额")],
            dimensions=[fake_dimension("region_name", "fact_order", ["大区"], ["华东"])],
        )
        patcher = patch("app.service.vector_service.semantic_layer", self.layer)
        patcher.start()
        self.addCleanup(patcher.stop)
        bm25 = patch("app.service.hybrid_retriever.hybrid_retriever", MagicMock(
            is_indexed=True,
            bm25_metrics=MagicMock(search=MagicMock(return_value=[])),
            bm25_dims=MagicMock(search=MagicMock(return_value=[])),
            fuse_rrf=MagicMock(side_effect=lambda dense, sparse: dense),
        ))
        bm25.start()
        self.addCleanup(bm25.stop)

    def _record_filters(self):
        captured = []
        original = self.svc.client.query_points

        def spy(*args, **kwargs):
            captured.append(kwargs.get("query_filter"))
            return original(*args, **kwargs)

        self.svc.client.query_points = spy
        return captured

    def test_filter_pins_embedding_model_and_source(self):
        query_filter = self.svc.build_query_filter(ONLINE_EMBEDDING_MODEL, "src-a")
        keys = {c.key: c.match.value for c in query_filter.must}
        self.assertEqual(keys["embedding_model"], ONLINE_EMBEDDING_MODEL)
        self.assertEqual(keys["source_id"], "src-a")

    def test_every_recall_path_passes_a_query_filter(self):
        self.svc.ingest_metadata()
        self.svc.ingest_fewshot_examples()
        self.svc.upsert_error_correction(
            {"question": "q", "error_message": "e", "wrong_sql": "a", "corrected_sql": "b"})
        captured = self._record_filters()
        self.svc.recall_semantic_meta("华东区销售额")   # value + metrics + dims = 3 次
        self.svc.recall_fewshot_examples("华东区销售额")  # 1 次
        self.svc.recall_error_corrections("华东区销售额", "err")  # 1 次
        self.assertEqual(len(captured), 5)
        for query_filter in captured:
            self.assertIsNotNone(query_filter)
            self.assertIn("embedding_model", {c.key for c in query_filter.must})

    def test_prefilter_can_be_switched_off_for_troubleshooting(self):
        self.svc.enable_prefilter = False
        self.assertIsNone(self.svc.build_query_filter(HASH_EMBEDDING_MODEL, "src-a"))

    def test_exclude_degraded_adds_a_must_not_clause(self):
        self.svc.exclude_degraded_on_recall = True
        query_filter = self.svc.build_query_filter(HASH_EMBEDDING_MODEL, "src-a")
        self.assertEqual({c.key for c in query_filter.must_not}, {"status"})

    def test_recall_hides_governance_fields_from_the_model_prompt(self):
        """召回结果整包 JSON 进提示词，治理字段不该混进去。"""
        self.svc.ingest_metadata()
        plain = self.svc.recall_semantic_meta("华东区销售额")
        self.assertTrue(plain)
        for item in plain:
            self.assertNotIn("embedding_model", item)
            self.assertNotIn("content_hash", item)
        traced = self.svc.recall_semantic_meta("华东区销售额", with_provenance=True)
        self.assertTrue(any("embedding_model" in item for item in traced))


class CrossModelIsolationTests(unittest.TestCase):
    """混存的哈希向量与 ada-002 向量之间比出来的分数没有意义，必须互不可见。"""

    def setUp(self):
        self.svc = build_service()

    def _seed_mixed_collection(self):
        collection = self.svc.error_correction_collection
        self.svc.client.upsert(collection_name=collection, points=[
            PointStruct(id=derive_point_id(collection, GLOBAL_SOURCE_ID, "hash-point"),
                        vector=[1.0] + [0.0] * (TEST_DIM - 1),
                        payload={"question": "hash-point", "error_message": "e",
                                 "wrong_sql": "a", "corrected_sql": "b",
                                 "source_id": GLOBAL_SOURCE_ID,
                                 "embedding_model": HASH_EMBEDDING_MODEL,
                                 "status": STATUS_ACTIVE}),
            PointStruct(id=derive_point_id(collection, GLOBAL_SOURCE_ID, "online-point"),
                        vector=[1.0] + [0.0] * (TEST_DIM - 1),
                        payload={"question": "online-point", "error_message": "e",
                                 "wrong_sql": "a", "corrected_sql": "b",
                                 "source_id": GLOBAL_SOURCE_ID,
                                 "embedding_model": ONLINE_EMBEDDING_MODEL,
                                 "status": STATUS_ACTIVE}),
        ])

    def test_hash_query_never_matches_online_points(self):
        self._seed_mixed_collection()
        with patch.object(self.svc, "_embed_for_query",
                          return_value=([1.0] + [0.0] * (TEST_DIM - 1), HASH_EMBEDDING_MODEL)):
            hits = self.svc.recall_error_corrections("anything", "", limit=5)
        self.assertEqual([h["question"] for h in hits], ["hash-point"])

    def test_online_query_never_matches_degraded_hash_points(self):
        self._seed_mixed_collection()
        with patch.object(self.svc, "_embed_for_query",
                          return_value=([1.0] + [0.0] * (TEST_DIM - 1), ONLINE_EMBEDDING_MODEL)):
            hits = self.svc.recall_error_corrections("anything", "", limit=5)
        self.assertEqual([h["question"] for h in hits], ["online-point"])

    def test_without_the_filter_both_spaces_would_be_ranked_together(self):
        """反证：关掉预过滤就回到了混排的旧行为。"""
        self._seed_mixed_collection()
        self.svc.enable_prefilter = False
        with patch.object(self.svc, "_embed_for_query",
                          return_value=([1.0] + [0.0] * (TEST_DIM - 1), HASH_EMBEDDING_MODEL)):
            hits = self.svc.recall_error_corrections("anything", "", limit=5)
        self.assertEqual(len(hits), 2)


class IncrementalCorrectionTests(unittest.TestCase):
    """旧实现每记一条经验就 delete_collection + 全量重嵌，还卡在问数主链路上。"""

    def setUp(self):
        self.svc = build_service()
        self.records = []
        memory = SimpleNamespace(get_error_corrections=lambda: self.records)
        patcher = patch("app.model.user_memory.user_memory", memory)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _record(n):
        return {"question": f"q{n}", "error_message": f"e{n}",
                "wrong_sql": f"wrong{n}", "corrected_sql": f"right{n}"}

    def test_sync_does_not_drop_the_collection(self):
        self.records = [self._record(1)]
        with patch.object(self.svc.client, "delete_collection",
                          side_effect=AssertionError("incremental sync must not rebuild the collection")):
            self.svc.ingest_error_corrections()
        self.assertEqual(len(scroll_all(self.svc, self.svc.error_correction_collection)), 1)

    def test_adding_one_record_embeds_only_that_record(self):
        self.records = [self._record(i) for i in range(5)]
        self.svc.ingest_error_corrections()
        self.records.append(self._record(99))
        with patch.object(self.svc, "_embed_for_index", wraps=self.svc._embed_for_index) as embed:
            stats = self.svc.ingest_error_corrections()
        self.assertEqual(embed.call_count, 1)
        self.assertEqual(stats, {"total": 6, "embedded": 1, "reused": 5})

    def test_repeated_sync_is_idempotent(self):
        self.records = [self._record(1), self._record(2)]
        self.svc.ingest_error_corrections()
        before = point_ids(self.svc, self.svc.error_correction_collection)
        for _ in range(3):
            self.svc.ingest_error_corrections()
        self.assertEqual(before, point_ids(self.svc, self.svc.error_correction_collection))
        self.assertEqual(len(before), 2)

    def test_deleted_record_is_removed_without_a_rebuild(self):
        """删除仍然是物理生效的 —— 剪枝取代了 delete_collection。"""
        self.records = [self._record(1), self._record(2)]
        self.svc.ingest_error_corrections()
        self.records.pop()
        self.svc.ingest_error_corrections()
        questions = [r.payload["question"] for r in scroll_all(self.svc, self.svc.error_correction_collection)]
        self.assertEqual(questions, ["q1"])

    def test_clearing_memory_empties_the_index(self):
        self.records = [self._record(1)]
        self.svc.ingest_error_corrections()
        self.records = []
        self.svc.ingest_error_corrections()
        self.assertEqual(scroll_all(self.svc, self.svc.error_correction_collection), [])

    def test_single_upsert_is_o_one_and_idempotent(self):
        self.records = [self._record(i) for i in range(4)]
        self.svc.ingest_error_corrections()
        item = self._record(7)
        with patch.object(self.svc, "_embed_for_index", wraps=self.svc._embed_for_index) as embed:
            point_id = self.svc.upsert_error_correction(item)
            self.svc.upsert_error_correction(item)
        self.assertEqual(embed.call_count, 1)  # 第二次内容指纹未变，直接跳过
        self.assertEqual(point_id, self.svc.correction_point_id(item))
        self.assertEqual(len(scroll_all(self.svc, self.svc.error_correction_collection)), 5)

    def test_single_upsert_never_prunes_its_neighbours(self):
        self.records = [self._record(1), self._record(2)]
        self.svc.ingest_error_corrections()
        self.svc.upsert_error_correction(self._record(3))
        self.assertEqual(len(scroll_all(self.svc, self.svc.error_correction_collection)), 3)

    def test_async_sync_keeps_the_ask_path_free_of_embedding_io(self):
        self.records = [self._record(1)]
        worker = self.svc.ingest_error_corrections_async()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(scroll_all(self.svc, self.svc.error_correction_collection)), 1)

    def test_destructive_rebuild_stays_available_but_opt_in(self):
        self.records = [self._record(1)]
        self.svc.ingest_error_corrections()
        with patch.object(self.svc.client, "delete_collection",
                          wraps=self.svc.client.delete_collection) as drop:
            self.svc.ingest_error_corrections(rebuild=True)
        drop.assert_called_once()
        self.assertEqual(len(scroll_all(self.svc, self.svc.error_correction_collection)), 1)


class LiveSingletonTests(unittest.TestCase):
    """对真实单例做只读/幂等校验，确认治理在真实语义层上也成立。"""

    def test_live_index_is_idempotent_and_traceable(self):
        before = {c: point_ids(vector_service, c) for c in
                  (vector_service.metrics_collection, vector_service.dims_collection)}
        vector_service.ingest_metadata()
        after = {c: point_ids(vector_service, c) for c in
                 (vector_service.metrics_collection, vector_service.dims_collection)}
        self.assertEqual(before, after)
        for collection in before:
            for record in scroll_all(vector_service, collection):
                self.assertIn("source_id", record.payload)
                self.assertIn("embedding_model", record.payload)
                self.assertIn("status", record.payload)

    def test_live_index_has_no_mixed_embedding_spaces(self):
        report = vector_service.describe_index()
        for name, stats in report["collections"].items():
            if stats.get("points"):
                self.assertFalse(stats["mixed_embedding_models"],
                                 f"collection '{name}' mixes embedding models: {stats}")

    def test_live_recall_still_returns_candidates(self):
        results = vector_service.recall_semantic_meta("昨天各分类的播放量是多少")
        self.assertTrue(results)
        self.assertNotIn("embedding_model", results[0])


if __name__ == "__main__":
    unittest.main()
