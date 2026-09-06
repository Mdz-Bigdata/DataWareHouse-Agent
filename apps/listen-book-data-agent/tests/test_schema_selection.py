import unittest

from app.agent.schema_selection import (
    filter_island_tables,
    relationship_condition_column,
    score_by_literal_match,
    shortest_relationship_paths,
    without_sensitive_columns,
)
from app.entities.column_info import ColumnInfo
from app.entities.relationship_info import RelationshipInfo


class SchemaSelectionTest(unittest.TestCase):
    def test_sensitive_columns_are_excluded(self):
        columns = [
            ColumnInfo("album.id", "id", "bigint", "primary_key", [], "主键", [], "album"),
            ColumnInfo(
                "user.phone",
                "phone",
                "varchar",
                "dimension",
                [],
                "手机号",
                [],
                "user",
                sensitive=True,
            ),
        ]

        self.assertEqual([item.id for item in without_sensitive_columns(columns)], ["album.id"])

    def test_shortest_path_connects_bridge_table(self):
        relationships = [
            RelationshipInfo("album.author", "album", "author_id", "author", "id"),
            RelationshipInfo("author.org", "author", "organization_id", "organization", "id"),
            RelationshipInfo("album.category", "album", "category_id", "category", "id"),
        ]

        path = shortest_relationship_paths(["album", "organization"], relationships)

        self.assertEqual([item.id for item in path], ["album.author", "author.org"])

    def test_virtual_relationship_keeps_discriminator_column(self):
        relationship = RelationshipInfo(
            "comment.target.album",
            "comment",
            "target_id",
            "album",
            "id",
            condition="comment.target_type = 'album'",
            physical=False,
        )

        self.assertEqual(relationship_condition_column(relationship), "target_type")


class FilterIslandTablesTest(unittest.TestCase):
    """Phase 2.3：孤岛过滤测试。"""

    def test_filters_tables_not_in_any_relationship(self):
        relationships = [
            RelationshipInfo("album.author", "album", "author_id", "author", "id"),
        ]
        # album、author 在关系图里；orphan 是孤岛表
        table_ids = ["album", "author", "orphan"]
        result = filter_island_tables(table_ids, relationships)
        self.assertEqual(result, ["album", "author"])

    def test_returns_all_when_no_relationships(self):
        # 无 relationship 时保守原样返回（单表查询场景，不误杀）
        table_ids = ["album", "author"]
        result = filter_island_tables(table_ids, [])
        self.assertEqual(result, ["album", "author"])

    def test_returns_empty_for_empty_input(self):
        self.assertEqual(filter_island_tables([], []), [])

    def test_preserves_order(self):
        relationships = [
            RelationshipInfo("b.a", "b", "a_id", "a", "id"),
        ]
        table_ids = ["a", "b", "c"]  # c 是孤岛
        result = filter_island_tables(table_ids, relationships)
        self.assertEqual(result, ["a", "b"])


class ScoreByLiteralMatchTest(unittest.TestCase):
    """Phase 2.3：字面提权测试。"""

    def test_name_match_scores_highest(self):
        candidates = [
            {"name": "play_count", "alias": [], "desc": "播放次数"},
            {"name": "retention", "alias": [], "desc": "留存"},
        ]
        result = score_by_literal_match(
            candidates,
            query="统计play_count",
            name_of=lambda c: c["name"],
            alias_of=lambda c: c["alias"],
            description_of=lambda c: c["desc"],
        )
        # play_count 命中 name(+10) 应排第一
        self.assertEqual(result[0][0]["name"], "play_count")
        self.assertEqual(result[0][1], 10)
        self.assertEqual(result[1][1], 0)

    def test_alias_match_scores_medium(self):
        candidates = [
            {"name": "m1", "alias": ["完播率"], "desc": "指标一"},
            {"name": "m2", "alias": [], "desc": "指标二"},
        ]
        result = score_by_literal_match(
            candidates,
            query="查询完播率",
            name_of=lambda c: c["name"],
            alias_of=lambda c: c["alias"],
            description_of=lambda c: c["desc"],
        )
        self.assertEqual(result[0][0]["name"], "m1")
        self.assertEqual(result[0][1], 5)

    def test_unmatched_candidates_kept_with_zero_score(self):
        # 未命中的对象保留在结果里，分数为 0（只重排不过滤）
        candidates = [
            {"name": "a", "alias": [], "desc": "x"},
            {"name": "b", "alias": [], "desc": "y"},
        ]
        result = score_by_literal_match(
            candidates,
            query="完全不相关",
            name_of=lambda c: c["name"],
            alias_of=lambda c: c["alias"],
            description_of=lambda c: c["desc"],
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(all(score == 0 for _, score in result))

    def test_empty_query_returns_original_order(self):
        candidates = [{"name": "a", "alias": [], "desc": "x"}]
        result = score_by_literal_match(
            candidates,
            query="",
            name_of=lambda c: c["name"],
            alias_of=lambda c: c["alias"],
            description_of=lambda c: c["desc"],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], 0)


if __name__ == "__main__":
    unittest.main()
