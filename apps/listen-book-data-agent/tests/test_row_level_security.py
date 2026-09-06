"""Phase 1.2 + 1.4：行级数据权限测试。"""

import unittest

from app.services.access_policy import AccessPolicyError
from app.services.row_level_security import (
    parse_data_scope,
    scope_constraints_to_dict_list,
)
from app.services.sql_guard import SQLSafetyError, validate_and_normalize_sql

# 测试用表结构：region 和 category 列均可唯一定位
SCOPE_TABLE_INFOS = [
    {
        "name": "audio_album",
        "columns": [
            {"name": "id"},
            {"name": "title"},
            {"name": "region"},
            {"name": "category"},
        ],
    }
]


class ParseDataScopeTest(unittest.TestCase):
    """Phase 1.4：data_scope JSON 解析测试。"""

    def test_parses_valid_multi_dimension_scope(self):
        data_scope = (
            '[{"column": "region", "value": "华东"}, {"column": "category", "value": "audio"}]'
        )
        constraints = parse_data_scope(data_scope)
        self.assertEqual(len(constraints), 2)
        self.assertEqual(constraints[0].column, "region")
        self.assertEqual(constraints[0].value, "华东")
        self.assertEqual(constraints[1].column, "category")
        self.assertEqual(constraints[1].value, "audio")

    def test_rejects_none_or_empty(self):
        for value in (None, "", "   "):
            with self.subTest(value=value), self.assertRaises(AccessPolicyError):
                parse_data_scope(value)

    def test_rejects_invalid_json(self):
        for value in ("not-a-json", "{broken"):
            with self.subTest(value=value), self.assertRaises(AccessPolicyError):
                parse_data_scope(value)

    def test_rejects_non_list_json(self):
        with self.assertRaises(AccessPolicyError):
            parse_data_scope('{"column": "region"}')

    def test_rejects_items_missing_column_or_value(self):
        data_scope = '[{"column": "region"}, {"value": "x"}, {"column": "ok", "value": "y"}]'
        with self.assertRaises(AccessPolicyError):
            parse_data_scope(data_scope)

    def test_serializes_to_dict_list(self):
        constraints = parse_data_scope('[{"column": "region", "value": "华东"}]')
        dict_list = scope_constraints_to_dict_list(constraints)
        self.assertEqual(dict_list, [{"column": "region", "value": "华东"}])


class RowLevelScopeInjectionTest(unittest.TestCase):
    """Phase 1.2：WHERE 注入测试。"""

    def test_injects_where_when_no_original_where(self):
        # 原始 SQL 无 WHERE，注入后应包含辖区条件
        safe_sql = validate_and_normalize_sql(
            "SELECT title FROM audio_album",
            SCOPE_TABLE_INFOS,
            500,
            row_level_scope=[{"column": "region", "value": "华东"}],
        )
        self.assertIn("region", safe_sql.sql.lower())
        self.assertIn("华东", safe_sql.sql)

    def test_merges_with_existing_where(self):
        # 原始 SQL 有 WHERE，注入后应 AND 合并
        safe_sql = validate_and_normalize_sql(
            "SELECT title FROM audio_album WHERE id > 0",
            SCOPE_TABLE_INFOS,
            500,
            row_level_scope=[{"column": "region", "value": "华东"}],
        )
        # 应同时包含原条件和注入条件
        sql_lower = safe_sql.sql.lower()
        self.assertIn("id > 0", sql_lower)
        self.assertIn("region", sql_lower)
        # 用 AND 连接
        self.assertIn("and", sql_lower)

    def test_injects_multiple_constraints_with_and(self):
        # 多维度约束，全部注入
        safe_sql = validate_and_normalize_sql(
            "SELECT title FROM audio_album",
            SCOPE_TABLE_INFOS,
            500,
            row_level_scope=[
                {"column": "region", "value": "华东"},
                {"column": "category", "value": "audio"},
            ],
        )
        sql_lower = safe_sql.sql.lower()
        self.assertIn("region", sql_lower)
        self.assertIn("category", sql_lower)
        self.assertIn("华东", safe_sql.sql)
        self.assertIn("audio", safe_sql.sql)

    def test_no_injection_when_scope_empty(self):
        # 空约束列表不注入（admin 全量可见）
        safe_sql = validate_and_normalize_sql(
            "SELECT title FROM audio_album",
            SCOPE_TABLE_INFOS,
            500,
            row_level_scope=[],
        )
        self.assertNotIn("WHERE", safe_sql.sql.upper())

    def test_no_injection_when_scope_none(self):
        # None 等价于不启用（向后兼容）
        safe_sql = validate_and_normalize_sql(
            "SELECT title FROM audio_album",
            SCOPE_TABLE_INFOS,
            500,
        )
        self.assertNotIn("WHERE", safe_sql.sql.upper())

    def test_rejects_scope_column_not_in_authorized_tables(self):
        # 注入列不在授权表里：拒绝（防配置错误越权）
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT title FROM audio_album",
                SCOPE_TABLE_INFOS,
                500,
                row_level_scope=[{"column": "nonexistent_col", "value": "x"}],
            )


if __name__ == "__main__":
    unittest.main()
