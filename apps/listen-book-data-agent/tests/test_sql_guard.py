import unittest

from app.agent.graph import route_after_sql_execution, route_after_sql_validation
from app.conf.app_config import app_config
from app.services.sql_guard import (
    SQLSafetyError,
    extract_sensitive_columns,
    validate_and_normalize_sql,
)

TABLE_INFOS = [
    {
        "name": "audio_album",
        "columns": [{"name": "id"}, {"name": "title"}, {"name": "album_status"}],
    },
    {
        "name": "play_session",
        "columns": [{"name": "id"}, {"name": "album_id"}, {"name": "played_seconds"}],
    },
]

# Phase 1.3 测试用：含敏感列（user_phone 标记为敏感）的表结构
SENSITIVE_TABLE_INFOS = [
    {
        "name": "app_user",
        "columns": [
            {"name": "id"},
            {"name": "nickname"},
            {"name": "user_phone", "sensitive": True},
            {"name": "region"},
        ],
    },
    {
        "name": "play_session",
        "columns": [{"name": "id"}, {"name": "user_id"}, {"name": "played_seconds"}],
    },
]


class SQLGuardTest(unittest.TestCase):
    def test_normalizes_and_caps_limit(self):
        safe_sql = validate_and_normalize_sql(
            "SELECT id, title FROM audio_album LIMIT 999", TABLE_INFOS, 500
        )

        self.assertEqual(safe_sql.limit, 500)
        self.assertEqual(safe_sql.sql, "SELECT id, title FROM audio_album LIMIT 500")

    def test_allows_count_star_and_table_alias(self):
        safe_sql = validate_and_normalize_sql(
            "SELECT COUNT(*) AS total FROM audio_album AS album", TABLE_INFOS, 500
        )

        self.assertEqual(safe_sql.limit, 500)
        self.assertIn("COUNT(*)", safe_sql.sql)

    def test_rejects_multiple_and_non_select_statements(self):
        for sql in (
            "SELECT id FROM audio_album; DELETE FROM audio_album",
            "DELETE FROM audio_album",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(sql, TABLE_INFOS, 500)

    def test_rejects_unknown_or_sensitive_columns_and_tables(self):
        for sql in (
            "SELECT phone FROM audio_album",
            "SELECT email FROM audio_album",
            "SELECT message_content FROM audio_album",
            "SELECT id FROM information_schema.tables",
            "SELECT * FROM audio_album",
            "SELECT LOAD_FILE('/tmp/private')",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(sql, TABLE_INFOS, 500)

    def test_rejects_ambiguous_unqualified_join_column(self):
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT id FROM audio_album JOIN play_session ON audio_album.id = play_session.album_id",
                TABLE_INFOS,
                500,
            )

    def test_rejects_write_lock_and_offset(self):
        for sql in (
            "SELECT id FROM audio_album FOR UPDATE",
            "SELECT id FROM audio_album LIMIT 10 OFFSET 10",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(sql, TABLE_INFOS, 500)

    def test_corrected_sql_is_revalidated_before_execution(self):
        self.assertEqual(route_after_sql_validation({"error": None}), "execute_sql")
        self.assertEqual(
            route_after_sql_validation({"error": "bad sql", "correction_attempts": 0}),
            "correct_sql",
        )
        self.assertEqual(
            route_after_sql_validation(
                {
                    "error": "still bad",
                    "correction_attempts": app_config.query.correction_attempts,
                }
            ),
            "report_sql_error",
        )
        self.assertEqual(
            route_after_sql_execution({"error": None}), "generate_chart_spec"
        )
        self.assertEqual(
            route_after_sql_execution({"error": "unknown column", "correction_attempts": 0}),
            "correct_sql",
        )
        self.assertEqual(
            route_after_sql_execution(
                {
                    "error": "still invalid",
                    "correction_attempts": 2,
                }
            ),
            "report_sql_error",
        )

    def test_dsl_guard_failure_uses_sql_refiner(self):
        self.assertEqual(
            route_after_sql_validation(
                {
                    "error": "unknown column",
                    "correction_attempts": 0,
                    "generation_mode": "dsl",
                    "generation_source": "dsl_compiled",
                }
            ),
            "correct_sql",
        )

    def test_allows_select_alias_references_in_order_group_having(self):
        table_infos = [
            {
                "name": "search_keyword_stat",
                "columns": [
                    {"name": "stat_date"},
                    {"name": "keyword"},
                    {"name": "search_count"},
                ],
            }
        ]
        # 线上失败原案：热搜榜月榜搜索词，ORDER BY 引用中文输出别名
        safe_sql = validate_and_normalize_sql(
            "SELECT DATE_FORMAT(stat_date, '%Y-%m') AS 月份, keyword AS 搜索词, "
            "SUM(search_count) AS 搜索次数 FROM search_keyword_stat "
            "GROUP BY DATE_FORMAT(stat_date, '%Y-%m'), keyword "
            "ORDER BY 月份 DESC, 搜索次数 DESC",
            table_infos,
            500,
        )
        self.assertEqual(safe_sql.limit, 500)

        safe_sql = validate_and_normalize_sql(
            "SELECT album_status AS 状态, COUNT(*) AS cnt FROM audio_album "
            "GROUP BY 状态 HAVING cnt > 1 ORDER BY cnt DESC",
            TABLE_INFOS,
            500,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_rejects_alias_reference_in_where(self):
        # WHERE / JOIN 不允许引用输出别名，必须落到真实授权字段
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT title AS 标题 FROM audio_album WHERE 标题 = 'x'",
                TABLE_INFOS,
                500,
            )


class SensitiveColumnGuardTest(unittest.TestCase):
    """Phase 1.3：列级敏感字段阻断测试。"""

    def test_extract_sensitive_columns(self):
        sensitive = extract_sensitive_columns(SENSITIVE_TABLE_INFOS)
        self.assertEqual(sensitive, {"app_user.user_phone"})

    def test_extract_sensitive_columns_empty_when_none_marked(self):
        # TABLE_INFOS 里没有标记 sensitive 的列，应返回空集合
        self.assertEqual(extract_sensitive_columns(TABLE_INFOS), set())

    def test_allows_query_when_sensitive_param_omitted(self):
        # 向后兼容：不传 sensitive_columns 时，即便列标记为敏感也不会拦截
        # （保护既有调用方与既有测试不被破坏）
        safe_sql = validate_and_normalize_sql(
            "SELECT user_phone FROM app_user",
            SENSITIVE_TABLE_INFOS,
            500,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_rejects_sensitive_column_with_qualifier(self):
        # 带表名限定的敏感列查询：应被拦截
        sensitive = extract_sensitive_columns(SENSITIVE_TABLE_INFOS)
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT app_user.user_phone FROM app_user",
                SENSITIVE_TABLE_INFOS,
                500,
                sensitive_columns=sensitive,
            )

    def test_rejects_sensitive_column_without_qualifier(self):
        # 不带表名限定的敏感列查询：保守策略，同样拦截
        sensitive = extract_sensitive_columns(SENSITIVE_TABLE_INFOS)
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT user_phone FROM app_user",
                SENSITIVE_TABLE_INFOS,
                500,
                sensitive_columns=sensitive,
            )

    def test_allows_non_sensitive_columns_with_sensitive_set(self):
        # 传入了敏感集合，但查询的是非敏感列：应正常通过
        sensitive = extract_sensitive_columns(SENSITIVE_TABLE_INFOS)
        safe_sql = validate_and_normalize_sql(
            "SELECT nickname, region FROM app_user",
            SENSITIVE_TABLE_INFOS,
            500,
            sensitive_columns=sensitive,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_rejects_sensitive_column_with_table_alias(self):
        # 带表别名的敏感列查询：别名应被正确还原为真实表名后拦截
        sensitive = extract_sensitive_columns(SENSITIVE_TABLE_INFOS)
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT u.user_phone FROM app_user AS u",
                SENSITIVE_TABLE_INFOS,
                500,
                sensitive_columns=sensitive,
            )


# Phase 1.1 测试用：授权关系（audio_album.id ← play_session.album_id）
AUTH_RELATIONSHIPS = [
    {
        "source_table": "play_session",
        "source_column": "album_id",
        "target_table": "audio_album",
        "target_column": "id",
    }
]


class JoinValidationGuardTest(unittest.TestCase):
    """Phase 1.1：笛卡尔积/多对多 JOIN 防御测试（严格模式）。"""

    def test_allows_authorized_join(self):
        # 命中授权关系的 JOIN（正向）：通过
        safe_sql = validate_and_normalize_sql(
            "SELECT audio_album.title FROM audio_album "
            "JOIN play_session ON audio_album.id = play_session.album_id",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_allows_authorized_join_reversed(self):
        # 命中授权关系的 JOIN（反向，ON 条件左右互换）：通过
        safe_sql = validate_and_normalize_sql(
            "SELECT audio_album.title FROM audio_album "
            "JOIN play_session ON play_session.album_id = audio_album.id",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_allows_authorized_join_with_aliases(self):
        # 带表别名的合法 JOIN：通过
        safe_sql = validate_and_normalize_sql(
            "SELECT a.title FROM audio_album AS a JOIN play_session AS p ON a.id = p.album_id",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_rejects_unauthorized_join(self):
        # 未授权的 JOIN 关系（编造连接条件）：严格模式拒绝
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT audio_album.title FROM audio_album "
                "JOIN play_session ON audio_album.id = play_session.id",
                TABLE_INFOS,
                500,
                relationships=AUTH_RELATIONSHIPS,
            )

    def test_rejects_join_without_on(self):
        # 无 ON 条件的 JOIN（隐式笛卡尔积）：拒绝
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT audio_album.title FROM audio_album JOIN play_session",
                TABLE_INFOS,
                500,
                relationships=AUTH_RELATIONSHIPS,
            )

    def test_rejects_join_with_non_equijoin_on(self):
        # ON 条件不含表间等值（只有常量比较）：拒绝
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT audio_album.title FROM audio_album JOIN play_session ON 1 = 1",
                TABLE_INFOS,
                500,
                relationships=AUTH_RELATIONSHIPS,
            )

    def test_allows_join_with_and_extra_conditions(self):
        # 复杂 ON（AND 连接等值 + 常量过滤）：合法等值命中即可通过
        safe_sql = validate_and_normalize_sql(
            "SELECT audio_album.title FROM audio_album "
            "JOIN play_session ON audio_album.id = play_session.album_id "
            "AND play_session.played_seconds > 100",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_allows_query_without_join_when_relationships_provided(self):
        # 提供 relationships 但 SQL 无 JOIN：不应触发校验，正常通过
        safe_sql = validate_and_normalize_sql(
            "SELECT id, title FROM audio_album",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_sql.limit, 500)

    def test_rejects_join_when_relationship_catalog_is_missing_or_empty(self):
        sql = (
            "SELECT audio_album.title FROM audio_album "
            "JOIN play_session ON audio_album.id = play_session.album_id"
        )
        for relationships in (None, []):
            with self.subTest(relationships=relationships), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(
                    sql,
                    TABLE_INFOS,
                    500,
                    relationships=relationships,
                )

    def test_rejects_cross_and_implicit_cartesian_joins(self):
        for sql in (
            "SELECT audio_album.title FROM audio_album CROSS JOIN play_session",
            "SELECT audio_album.title FROM audio_album, play_session",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(
                    sql,
                    TABLE_INFOS,
                    500,
                    relationships=AUTH_RELATIONSHIPS,
                )

    def test_rejects_or_in_join_even_when_one_branch_is_authorized(self):
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT audio_album.title FROM audio_album JOIN play_session "
                "ON audio_album.id = play_session.album_id OR 1 = 1",
                TABLE_INFOS,
                500,
                relationships=AUTH_RELATIONSHIPS,
            )

    def test_rejects_tautological_or_in_where(self):
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT title FROM audio_album WHERE album_status = 'active' OR 1 = 1",
                TABLE_INFOS,
                500,
            )

    def test_each_join_must_connect_the_new_table(self):
        table_infos = [
            {"name": "table_a", "columns": [{"name": "id"}]},
            {"name": "table_b", "columns": [{"name": "id"}, {"name": "a_id"}]},
            {"name": "table_c", "columns": [{"name": "id"}]},
        ]
        relationships = [
            {
                "source_table": "table_b",
                "source_column": "a_id",
                "target_table": "table_a",
                "target_column": "id",
            }
        ]
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT table_a.id FROM table_a "
                "JOIN table_b ON table_a.id = table_b.a_id "
                "JOIN table_c ON table_a.id = table_b.a_id",
                table_infos,
                500,
                relationships=relationships,
            )


class ScopedSQLGuardTest(unittest.TestCase):
    def test_allows_authorized_cte_and_subquery(self):
        cte_sql = (
            "WITH recent AS ("
            "SELECT album_id, played_seconds FROM play_session WHERE played_seconds > 0"
            ") "
            "SELECT a.title, SUM(r.played_seconds) AS total FROM recent r "
            "JOIN audio_album a ON r.album_id = a.id "
            "GROUP BY a.title ORDER BY total DESC"
        )
        safe_cte = validate_and_normalize_sql(
            cte_sql,
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_cte.tables, ("audio_album", "play_session"))

        safe_subquery = validate_and_normalize_sql(
            "SELECT a.title FROM audio_album a WHERE a.id IN "
            "(SELECT p.album_id FROM play_session p WHERE p.played_seconds > 0)",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_subquery.tables, ("audio_album", "play_session"))

        safe_correlated = validate_and_normalize_sql(
            "SELECT a.title FROM audio_album a WHERE EXISTS "
            "(SELECT 1 FROM play_session p WHERE p.album_id = a.id)",
            TABLE_INFOS,
            500,
            relationships=AUTH_RELATIONSHIPS,
        )
        self.assertEqual(safe_correlated.tables, ("audio_album", "play_session"))

    def test_rejects_disconnected_nested_multi_table_query(self):
        with self.assertRaises(SQLSafetyError):
            validate_and_normalize_sql(
                "SELECT a.title, (SELECT COUNT(*) FROM play_session) AS plays FROM audio_album a",
                TABLE_INFOS,
                500,
                relationships=AUTH_RELATIONSHIPS,
            )

    def test_validates_columns_in_all_clauses_and_nested_scopes(self):
        invalid_queries = (
            "SELECT missing FROM audio_album",
            "SELECT title FROM audio_album WHERE missing = 1",
            "SELECT a.title FROM audio_album a JOIN play_session p ON a.id = p.missing",
            "SELECT title FROM audio_album GROUP BY missing",
            "SELECT title FROM audio_album ORDER BY missing",
            "SELECT COUNT(*) AS total FROM audio_album HAVING missing > 1",
            "WITH bad AS (SELECT missing FROM audio_album) SELECT missing FROM bad",
            "SELECT title FROM audio_album WHERE id IN (SELECT missing FROM play_session)",
        )
        for sql in invalid_queries:
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(
                    sql,
                    TABLE_INFOS,
                    500,
                    relationships=AUTH_RELATIONSHIPS,
                )

    def test_intersects_recalled_schema_with_policy_table_acl(self):
        safe = validate_and_normalize_sql(
            "SELECT title FROM audio_album",
            TABLE_INFOS,
            500,
            table_acl={"audio_album": ["title"]},
        )
        self.assertIn("title", safe.sql.lower())

        for sql in (
            "SELECT id FROM audio_album",
            "SELECT played_seconds FROM play_session",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(
                    sql,
                    TABLE_INFOS,
                    500,
                    table_acl={"audio_album": ["title"]},
                )

    def test_dialect_and_policy_function_allowlists_both_apply(self):
        table_infos = [{"name": "daily_stat", "columns": [{"name": "stat_date"}]}]
        safe = validate_and_normalize_sql(
            "SELECT DATE_FORMAT(stat_date, '%Y-%m') AS month FROM daily_stat",
            table_infos,
            500,
            allowed_functions=["DATE_FORMAT"],
        )
        self.assertIn("DATE_FORMAT", safe.sql)

        for sql, functions in (
            ("SELECT MD5(stat_date) FROM daily_stat", None),
            (
                "SELECT DATE_FORMAT(stat_date, '%Y-%m') FROM daily_stat",
                ["COUNT"],
            ),
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLSafetyError):
                validate_and_normalize_sql(
                    sql,
                    table_infos,
                    500,
                    allowed_functions=functions,
                )

    def test_rls_is_alias_aware_idempotent_and_nested(self):
        table_infos = [
            {
                "name": "orders",
                "columns": [
                    {"name": "id"},
                    {"name": "tenant_id"},
                    {"name": "amount"},
                ],
            }
        ]
        scope = [
            {
                "table": "orders",
                "column": "tenant_id",
                "operator": "eq",
                "value": "tenant-1",
            }
        ]
        first = validate_and_normalize_sql(
            "WITH scoped AS (SELECT id, amount FROM orders o) SELECT id, amount FROM scoped",
            table_infos,
            500,
            row_level_scope=scope,
            table_acl={"orders": ["id", "tenant_id", "amount"]},
        )
        second = validate_and_normalize_sql(
            first.sql,
            table_infos,
            500,
            row_level_scope=scope,
            table_acl={"orders": ["id", "tenant_id", "amount"]},
        )

        self.assertEqual(first.sql, second.sql)
        self.assertIn("o.tenant_id = 'tenant-1'", first.sql)
        self.assertEqual(first.sql.count("tenant_id"), 1)


if __name__ == "__main__":
    unittest.main()
