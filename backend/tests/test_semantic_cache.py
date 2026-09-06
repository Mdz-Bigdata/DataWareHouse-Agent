"""Response-contract and isolation regressions for cached query results."""
import copy
import unittest

from app.schema.chat import AskResponse
from app.service.semantic_cache import CacheItem, SemanticCache, semantic_cache


QUESTION = "昨天听书各分类播放量是多少"


def valid_response():
    return {
        "success": True,
        "data": [{"category_name": "历史", "play_count": 17}],
        "chart": {"type": "bar", "title": "播放量", "config": {"series": []}},
        "details": {
            "sql": "SELECT category_name, SUM(play_count) FROM audio GROUP BY category_name",
            "dialect": "doris",
            "elapsed_time": "0.01s",
            "tables": ["audio"],
            "source_desc": "test database",
            "filters": [],
        },
    }


def invalid_response():
    result = valid_response()
    del result["chart"]["config"]
    del result["details"]["sql"]
    del result["details"]["filters"]
    return result


class SemanticCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = SemanticCache()

    def inject_legacy_item(self, response, question=QUESTION):
        key = self.cache._generate_key(question, "doris", "user")
        item = CacheItem(key, question, response, embedding=[1.0, 0.0])
        self.cache.exact_cache[key] = item
        self.cache.semantic_items.append(item)
        return item

    def test_singleton_does_not_preload_unexecuted_audio_results(self):
        self.assertNotIn(
            semantic_cache._generate_key(QUESTION, "doris", "user"),
            semantic_cache.exact_cache,
        )
        self.assertNotIn(
            semantic_cache._generate_key("听书完播率排名前5的专辑", "doris", "user"),
            semantic_cache.exact_cache,
        )

    def test_malformed_write_is_not_cached(self):
        self.cache.put(QUESTION, "doris", "user", invalid_response(), [1.0, 0.0])
        self.assertEqual(self.cache.exact_cache, {})
        self.assertEqual(self.cache.semantic_items, [])

    def test_invalid_exact_entry_is_evicted_from_both_tiers(self):
        self.inject_legacy_item(invalid_response())
        self.assertIsNone(self.cache.get(QUESTION, query_embedding=[1.0, 0.0]))
        self.assertEqual(self.cache.exact_cache, {})
        self.assertEqual(self.cache.semantic_items, [])
        self.assertEqual(self.cache.exact_hits, 0)

    def test_invalid_semantic_entry_is_evicted_from_both_tiers(self):
        self.inject_legacy_item(invalid_response())
        self.assertIsNone(self.cache.get(QUESTION + "呢", query_embedding=[1.0, 0.0]))
        self.assertEqual(self.cache.exact_cache, {})
        self.assertEqual(self.cache.semantic_items, [])
        self.assertEqual(self.cache.semantic_hits, 0)

    def test_valid_candidate_survives_invalid_semantic_neighbor(self):
        self.inject_legacy_item(invalid_response())
        self.cache.put(QUESTION + "？", "doris", "user", valid_response(), [1.0, 0.01])
        response, hit_type = self.cache.get(QUESTION + "呢", query_embedding=[1.0, 0.0])
        self.assertEqual(hit_type, "semantic")
        self.assertEqual(response["matched_question"], QUESTION + "？")
        AskResponse.model_validate(response)

    def test_valid_exact_and_semantic_results_meet_api_contract(self):
        self.cache.put(QUESTION, "doris", "user", valid_response(), [1.0, 0.0])
        for query, expected_type in [(QUESTION, "exact"), (QUESTION + "呢", "semantic")]:
            with self.subTest(query=query):
                response, hit_type = self.cache.get(query, query_embedding=[1.0, 0.0])
                self.assertEqual(hit_type, expected_type)
                self.assertTrue(AskResponse.model_validate(response).cache_hit)
                self.assertEqual(response["data"][0]["play_count"], 17)

    def test_input_and_output_mutation_does_not_corrupt_cached_result(self):
        response = valid_response()
        embedding = [1.0, 0.0]
        self.cache.put(QUESTION, "doris", "user", response, embedding)
        response["data"][0]["play_count"] = 999
        embedding[0] = 0.0
        hit, _ = self.cache.get(QUESTION)
        self.assertEqual(hit["data"][0]["play_count"], 17)
        del hit["details"]["sql"]
        second_hit, _ = self.cache.get(QUESTION + "呢", query_embedding=[1.0, 0.0])
        AskResponse.model_validate(second_hit)

    def test_semantic_lookup_respects_role_and_dialect(self):
        self.cache.put(QUESTION, "doris", "admin", valid_response(), [1.0, 0.0])
        for role, dialect in [("user", "doris"), ("admin", "postgres")]:
            with self.subTest(role=role, dialect=dialect):
                self.assertIsNone(self.cache.get(
                    QUESTION, role=role, dialect=dialect, query_embedding=[1.0, 0.0]
                ))

    def test_replacement_removes_old_semantic_result(self):
        old = valid_response()
        self.cache.put(QUESTION, "doris", "user", old, [1.0, 0.0])
        new = copy.deepcopy(old)
        new["data"][0]["play_count"] = 23
        self.cache.put(QUESTION, "doris", "user", new, [1.0, 0.0])
        response, _ = self.cache.get(QUESTION + "呢", query_embedding=[1.0, 0.0])
        self.assertEqual(response["data"][0]["play_count"], 23)
        self.assertEqual(len(self.cache.semantic_items), 1)

    def test_failures_and_clarifications_are_never_cached(self):
        for response in [
            {"success": False, "error": "Missing metric"},
            {"success": True, "clarification": {"need_clarification": True}},
        ]:
            with self.subTest(response=response):
                self.cache.put(QUESTION, "doris", "user", response, [1.0, 0.0])
                self.assertIsNone(self.cache.get(QUESTION, query_embedding=[1.0, 0.0]))

    def test_legacy_failure_is_evicted(self):
        self.inject_legacy_item({"success": False, "error": "Missing metric"})
        self.assertIsNone(self.cache.get(QUESTION))
        self.assertEqual(self.cache.semantic_items, [])

    def test_expired_exact_item_is_removed_from_both_tiers(self):
        item = self.inject_legacy_item(valid_response())
        item.expire_at = 0
        self.assertIsNone(self.cache.get(QUESTION))
        self.assertEqual(self.cache.semantic_items, [])


if __name__ == "__main__":
    unittest.main()
