"""Phase 3.1 + 3.2：数据源方言策略测试。

验证四种方言（mysql/postgresql/clickhouse/doris）的策略实现正确性，
以及 sql_guard 的方言化解析/生成行为。
"""

import unittest

from app.repositories.dialect import get_dialect_strategy
from app.services.sql_guard import SQLSafetyError, validate_and_normalize_sql

TABLE_INFOS = [
    {
        "name": "audio_album",
        "columns": [{"name": "id"}, {"name": "title"}],
    }
]


class DialectStrategyTest(unittest.TestCase):
    """Phase 3.1：方言策略工厂与各实现测试。"""

    def test_get_mysql_dialect(self):
        d = get_dialect_strategy("mysql")
        self.assertEqual(d.name, "mysql")
        self.assertEqual(d.sqlglot_dialect, "mysql")
        self.assertEqual(d.drivername, "mysql+asyncmy")
        self.assertEqual(d.get_version_sql(), "SELECT VERSION()")
        self.assertEqual(d.explain_sql("SELECT 1"), "EXPLAIN SELECT 1")

    def test_get_postgresql_dialect(self):
        d = get_dialect_strategy("postgresql")
        self.assertEqual(d.name, "postgresql")
        self.assertEqual(d.sqlglot_dialect, "postgres")
        self.assertEqual(d.drivername, "postgresql+asyncpg")
        # PG 列类型查询用 information_schema
        sql = d.get_column_types_sql("myschema.album")
        self.assertIn("information_schema.columns", sql)
        self.assertIn("myschema", sql)
        self.assertIn("album", sql)

    def test_get_clickhouse_dialect(self):
        d = get_dialect_strategy("clickhouse")
        self.assertEqual(d.name, "clickhouse")
        self.assertEqual(d.sqlglot_dialect, "clickhouse")
        self.assertEqual(d.drivername, "clickhouse+asynch")
        # CK 列类型查询用 system.columns
        sql = d.get_column_types_sql("default.album")
        self.assertIn("system.columns", sql)

    def test_get_doris_dialect(self):
        d = get_dialect_strategy("doris")
        self.assertEqual(d.name, "doris")
        self.assertEqual(d.sqlglot_dialect, "mysql")  # Doris 用 mysql 方言解析
        self.assertEqual(d.drivername, "mysql+asyncmy")  # 兼容 MySQL 协议
        sql = d.get_column_types_sql("default.album")
        self.assertIn("INFORMATION_SCHEMA.COLUMNS", sql)

    def test_unknown_dialect_raises(self):
        with self.assertRaises(ValueError):
            get_dialect_strategy("oracle")

    def test_dialect_case_insensitive(self):
        # 大写方言名也应识别
        d = get_dialect_strategy("MySQL")
        self.assertEqual(d.name, "mysql")

    def test_distinct_values_sql_consistent_across_dialects(self):
        # 四种方言的去重值 SQL 结构一致（仅引擎差异）
        for name in ("mysql", "postgresql", "clickhouse", "doris"):
            d = get_dialect_strategy(name)
            sql = d.get_distinct_values_sql("album", "title", 10)
            self.assertIn("DISTINCT title", sql)
            self.assertIn("LIMIT 10", sql)


class SqlGuardDialectTest(unittest.TestCase):
    """Phase 3.2：sql_guard 方言化解析/生成测试。"""

    def test_default_dialect_is_mysql(self):
        # 不传 dialect 时默认 mysql（向后兼容）
        safe_sql = validate_and_normalize_sql(
            "SELECT id FROM audio_album",
            TABLE_INFOS,
            500,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_postgresql_dialect_parse_and_generate(self):
        # PG 方言解析（双引号标识符等 PG 特性）
        safe_sql = validate_and_normalize_sql(
            "SELECT id FROM audio_album",
            TABLE_INFOS,
            500,
            dialect="postgres",
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_clickhouse_dialect_parse_and_generate(self):
        safe_sql = validate_and_normalize_sql(
            "SELECT id FROM audio_album",
            TABLE_INFOS,
            500,
            dialect="clickhouse",
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_invalid_sql_raises_regardless_of_dialect(self):
        for dialect in ("mysql", "postgres", "clickhouse"):
            with self.subTest(dialect=dialect), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(
                    "DELETE FROM audio_album",
                    TABLE_INFOS,
                    500,
                    dialect=dialect,
                )


if __name__ == "__main__":
    unittest.main()
