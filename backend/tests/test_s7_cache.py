"""§7.3-1 跨源串数据：语义缓存必须按数据源隔离。

回归的是这样一个真实故障：缓存键只有 `role::dialect::question`，而 dialect 只是
SQL 方言。本系统支持 7 种引擎、同一引擎可以配置多个实例，两个 PostgreSQL 数据源
方言完全相同，于是在 A 源问过的问题，切到 B 源再问会直接返回 A 的结果。

隔离有两层，必须分别验证：
  L1  精确哈希键
  L2  语义向量近邻——向量绕过键，只有候选过滤拦得住
"""
import hashlib
import sys
import types
import unittest

from app.service.semantic_cache import UNRESOLVED_SOURCE_ID, SemanticCache, active_source_id


SOURCE_A = "pg-warehouse-a"
SOURCE_B = "pg-warehouse-b"
QUESTION = "昨天各品类的交易额是多少"
EMBEDDING = [1.0, 0.0]


def response(gmv: int, source_desc: str):
    """一条合法的问数结果；gmv 用来分辨它来自哪个数据源。"""
    return {
        "success": True,
        "data": [{"category_name": "历史", "gmv": gmv}],
        "chart": {"type": "bar", "title": "交易额", "config": {"series": []}},
        "details": {
            "sql": "SELECT category_name, SUM(gmv) FROM dws_trade_order_daily GROUP BY category_name",
            "dialect": "postgres",
            "elapsed_time": "0.01s",
            "tables": ["dws_trade_order_daily"],
            "source_desc": source_desc,
            "filters": [],
        },
    }


class FakeDatabase:
    """只提供缓存关心的那一个属性：目的地指纹。"""

    def __init__(self, identity: str):
        self.source_id = identity
        self.database_identity = identity


class ActiveSourceStub:
    """替换 sys.modules 里的 db_service 模块，模拟 activate() 切源。

    activate() 的真实做法是整体替换 db_service 的实例字典，所有 import 过它的模块
    都会看到新数据源；这里换掉模块上的单例，对缓存而言是等价的可观测行为。
    """

    def __init__(self, test: unittest.TestCase, identity: str):
        self.module = types.ModuleType("app.service.db_service")
        self.module.db_service = FakeDatabase(identity)
        previous = sys.modules.get("app.service.db_service")
        sys.modules["app.service.db_service"] = self.module
        test.addCleanup(self._restore, previous)

    @staticmethod
    def _restore(previous):
        if previous is None:
            sys.modules.pop("app.service.db_service", None)
        else:
            sys.modules["app.service.db_service"] = previous

    def activate(self, identity: str) -> None:
        self.module.db_service = FakeDatabase(identity)


class ExplicitSourceIsolationTests(unittest.TestCase):
    """调用方显式传 source_id 的路径。"""

    def setUp(self):
        self.cache = SemanticCache()

    def put(self, source_id, gmv, question=QUESTION, embedding=EMBEDDING):
        self.cache.put(question, "postgres", "user", response(gmv, source_id),
                       embedding, source_id=source_id)

    def test_exact_hit_does_not_cross_sources(self):
        self.put(SOURCE_A, 100)
        self.assertIsNone(self.cache.get(QUESTION, dialect="postgres", role="user", source_id=SOURCE_B))

    def test_semantic_neighbour_does_not_cross_sources(self):
        """近义问句在别的数据源上也不能命中——向量绕过 L1，这里考的是 L2。"""
        self.put(SOURCE_A, 100)
        self.assertIsNone(self.cache.get(QUESTION + "呢", dialect="postgres", role="user",
                                         query_embedding=EMBEDDING, source_id=SOURCE_B))
        self.assertEqual(self.cache.semantic_hits, 0)
        # 另一个源的条目不该被这次未命中顺手淘汰掉
        self.assertEqual(len(self.cache.semantic_items), 1)

    def test_same_question_returns_each_source_own_result(self):
        self.put(SOURCE_A, 100)
        self.put(SOURCE_B, 200)
        for source, expected in [(SOURCE_A, 100), (SOURCE_B, 200)]:
            with self.subTest(source=source):
                hit, hit_type = self.cache.get(QUESTION, dialect="postgres", role="user", source_id=source)
                self.assertEqual(hit_type, "exact")
                self.assertEqual(hit["data"][0]["gmv"], expected)
                self.assertEqual(hit["details"]["source_desc"], source)

    def test_same_source_still_hits_both_tiers(self):
        """隔离不能把缓存废掉：同源重复提问仍然要命中。"""
        self.put(SOURCE_A, 100)
        exact, exact_type = self.cache.get(QUESTION, dialect="postgres", role="user",
                                           query_embedding=EMBEDDING, source_id=SOURCE_A)
        self.assertEqual(exact_type, "exact")
        self.assertEqual(exact["data"][0]["gmv"], 100)
        semantic, semantic_type = self.cache.get(QUESTION + "呢", dialect="postgres", role="user",
                                                 query_embedding=EMBEDDING, source_id=SOURCE_A)
        self.assertEqual(semantic_type, "semantic")
        self.assertEqual(semantic["data"][0]["gmv"], 100)

    def test_dialect_alone_never_separates_two_sources_of_one_engine(self):
        """两个 PostgreSQL 源的 role/dialect 完全相同——只有数据源维度能分开它们。"""
        keys = {source: self.cache._generate_key(QUESTION, "postgres", "user", source)
                for source in (SOURCE_A, SOURCE_B)}
        self.assertNotEqual(keys[SOURCE_A], keys[SOURCE_B])
        # 旧键公式（role::dialect::question）对两个源给出同一个键，正是串数据的来源
        legacy = hashlib.sha256(
            f"user::postgres::{QUESTION.strip().lower().replace(' ', '')}".encode("utf-8")
        ).hexdigest()
        self.assertNotIn(legacy, keys.values())

    def test_source_is_case_insensitive_like_role_and_dialect(self):
        self.put(SOURCE_A, 100)
        hit, _ = self.cache.get(QUESTION, dialect="postgres", role="user", source_id=SOURCE_A.upper())
        self.assertEqual(hit["data"][0]["gmv"], 100)


class ActiveSourceSwitchTests(unittest.TestCase):
    """不传 source_id 的路径：缓存自己解析当前活跃数据源。

    这是 ask_agent 今天实际走的调用形式，也是 activate() 切源后必须失效的那条路。
    """

    def setUp(self):
        self.cache = SemanticCache()
        self.active = ActiveSourceStub(self, SOURCE_A)

    def put(self, gmv):
        self.cache.put(QUESTION, "postgres", "user", response(gmv, "active"), EMBEDDING)

    def test_switching_source_makes_the_previous_result_unreachable(self):
        self.put(100)
        self.assertIsNotNone(self.cache.get(QUESTION, dialect="postgres", role="user"))
        self.active.activate(SOURCE_B)
        self.assertIsNone(self.cache.get(QUESTION, dialect="postgres", role="user",
                                         query_embedding=EMBEDDING))

    def test_switching_back_reuses_the_original_source_cache(self):
        """键带数据源是隔离而不是清空：切回去应当仍然命中，不必重算。"""
        self.put(100)
        self.active.activate(SOURCE_B)
        self.assertIsNone(self.cache.get(QUESTION, dialect="postgres", role="user"))
        self.active.activate(SOURCE_A)
        hit, hit_type = self.cache.get(QUESTION, dialect="postgres", role="user")
        self.assertEqual(hit_type, "exact")
        self.assertEqual(hit["data"][0]["gmv"], 100)

    def test_each_source_keeps_its_own_answer_to_one_question(self):
        self.put(100)
        self.active.activate(SOURCE_B)
        self.put(200)
        for source, expected in [(SOURCE_A, 100), (SOURCE_B, 200)]:
            with self.subTest(source=source):
                self.active.activate(source)
                hit, _ = self.cache.get(QUESTION, dialect="postgres", role="user")
                self.assertEqual(hit["data"][0]["gmv"], expected)

    def test_items_written_without_a_source_belong_to_the_active_one(self):
        self.put(100)
        self.assertEqual([item.source_id for item in self.cache.semantic_items], [SOURCE_A])

    def test_stats_report_the_active_source(self):
        self.put(100)
        stats = self.cache.get_stats()
        self.assertEqual(stats["active_source_id"], SOURCE_A)
        self.assertEqual(stats["cached_entries"][0]["source_id"], SOURCE_A)

    def test_targeted_invalidation_spares_the_other_source(self):
        self.put(100)
        self.active.activate(SOURCE_B)
        self.put(200)
        self.assertEqual(self.cache.invalidate_source(SOURCE_B), 2)
        self.assertIsNone(self.cache.get(QUESTION, dialect="postgres", role="user"))
        self.active.activate(SOURCE_A)
        self.assertIsNotNone(self.cache.get(QUESTION, dialect="postgres", role="user"))


class SourceResolutionTests(unittest.TestCase):
    """解析数据源标识不得把建连副作用带进只用缓存的进程。"""

    def test_resolution_never_imports_the_database_module(self):
        previous = sys.modules.pop("app.service.db_service", None)
        if previous is not None:
            self.addCleanup(sys.modules.__setitem__, "app.service.db_service", previous)
        self.assertEqual(active_source_id(), UNRESOLVED_SOURCE_ID)
        self.assertNotIn("app.service.db_service", sys.modules)

    def test_half_initialised_database_degrades_instead_of_raising(self):
        class Exploding:
            @property
            def source_id(self):
                raise RuntimeError("connection is being rebound")

            @property
            def database_identity(self):
                raise RuntimeError("connection is being rebound")

        module = types.ModuleType("app.service.db_service")
        module.db_service = Exploding()
        previous = sys.modules.get("app.service.db_service")
        sys.modules["app.service.db_service"] = module
        self.addCleanup(ActiveSourceStub._restore, previous)
        self.assertEqual(active_source_id(), UNRESOLVED_SOURCE_ID)

    def test_unresolved_source_still_keeps_one_consistent_bucket(self):
        """解析失败时写入与读取必须用同一个哨兵值，否则缓存直接永不命中。"""
        cache = SemanticCache()
        module = types.ModuleType("app.service.db_service")
        module.db_service = None
        previous = sys.modules.get("app.service.db_service")
        sys.modules["app.service.db_service"] = module
        self.addCleanup(ActiveSourceStub._restore, previous)
        cache.put(QUESTION, "postgres", "user", response(100, "unbound"), EMBEDDING)
        self.assertEqual(cache.semantic_items[0].source_id, UNRESOLVED_SOURCE_ID)
        self.assertIsNotNone(cache.get(QUESTION, dialect="postgres", role="user"))


if __name__ == "__main__":
    unittest.main()
