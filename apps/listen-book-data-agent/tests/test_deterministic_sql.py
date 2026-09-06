import asyncio
import unittest
from types import SimpleNamespace

from app.agent.analysis_plan import build_analysis_plan
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.merge_retrieved_info import _prioritized_table_ids
from app.entities.column_info import ColumnInfo
from app.services.deterministic_sql_service import build_deterministic_sql
from app.services.sql_guard import (
    SQLSafetyError,
    extract_filter_only_columns,
    extract_sensitive_columns,
    validate_and_normalize_sql,
)


QUESTION = "北京地区男性黄金会员的播放总次数且玄幻和言情类有声书的平均播放时长差多少"

TABLE_INFOS = [
    {"name": "play_session", "columns": [
        {"name": "id"}, {"name": "user_id"}, {"name": "album_id"},
        {"name": "played_seconds"},
    ]},
    {"name": "user_account", "columns": [{"name": "id"}]},
    {"name": "user_profile", "columns": [
        {"name": "user_id"}, {"name": "gender"},
        {"name": "province", "sensitive": True, "filter_only": True},
        {"name": "city", "sensitive": True, "filter_only": True},
    ]},
    {"name": "member_account", "columns": [
        {"name": "user_id"}, {"name": "member_level"},
        {"name": "member_status"}, {"name": "valid_from"}, {"name": "valid_to"},
    ]},
    {"name": "audio_album", "columns": [{"name": "id"}, {"name": "category_id"}]},
    {"name": "dim_audio_category", "columns": [{"name": "id"}, {"name": "category_name"}]},
]

RELATIONSHIPS = [
    {"source_table": "play_session", "source_column": "user_id", "target_table": "user_account", "target_column": "id"},
    {"source_table": "user_profile", "source_column": "user_id", "target_table": "user_account", "target_column": "id"},
    {"source_table": "member_account", "source_column": "user_id", "target_table": "user_account", "target_column": "id"},
    {"source_table": "play_session", "source_column": "album_id", "target_table": "audio_album", "target_column": "id"},
    {"source_table": "audio_album", "source_column": "category_id", "target_table": "dim_audio_category", "target_column": "id"},
]


class DeterministicSQLTest(unittest.TestCase):
    def setUp(self):
        self.plan = build_analysis_plan(QUESTION).to_state()
        self.sql = build_deterministic_sql(self.plan, TABLE_INFOS)

    def test_builds_flat_sql_with_every_required_filter(self):
        self.assertIsNotNone(self.sql)
        self.assertNotIn("WITH ", self.sql.upper())
        self.assertIn("up.gender = 'male'", self.sql)
        self.assertIn("ma.member_level = 'vip'", self.sql)
        self.assertIn("up.province LIKE '%北京%'", self.sql)
        self.assertIn("dac.category_name LIKE '%玄幻%'", self.sql)
        self.assertIn("dac.category_name LIKE '%言情%'", self.sql)

    def test_metric_fact_table_is_relationship_path_root(self):
        columns = [
            ColumnInfo(
                id="member_account.member_level",
                name="member_level",
                type="varchar",
                role="dimension",
                examples=[],
                description="会员等级",
                alias=[],
                table_id="member_account",
            ),
            ColumnInfo(
                id="play_session.id",
                name="id",
                type="bigint",
                role="primary_key",
                examples=[],
                description="播放会话主键",
                alias=[],
                table_id="play_session",
            ),
        ]

        seeds = _prioritized_table_ids(columns, [], self.plan)

        self.assertEqual(seeds[0], "play_session")

    def test_guard_allows_filter_only_region_for_aggregate_and_checks_semantics(self):
        safe = validate_and_normalize_sql(
            self.sql or "",
            TABLE_INFOS,
            500,
            sensitive_columns=extract_sensitive_columns(TABLE_INFOS),
            filter_only_columns=extract_filter_only_columns(TABLE_INFOS),
            relationships=RELATIONSHIPS,
            analysis_plan=self.plan,
        )

        self.assertIn("COUNT(ps.id)", safe.sql)
        self.assertIn("up.province LIKE '%北京%'", safe.sql)

    def test_guard_rejects_false_success_that_drops_audience_filters(self):
        stripped_sql = (
            "SELECT COUNT(ps.id), "
            "AVG(CASE WHEN dac.category_name LIKE '%玄幻%' THEN ps.played_seconds END) - "
            "AVG(CASE WHEN dac.category_name LIKE '%言情%' THEN ps.played_seconds END) "
            "FROM play_session ps "
            "JOIN audio_album aa ON ps.album_id = aa.id "
            "JOIN dim_audio_category dac ON aa.category_id = dac.id"
        )
        with self.assertRaisesRegex(SQLSafetyError, "地区包含北京"):
            validate_and_normalize_sql(
                stripped_sql,
                TABLE_INFOS,
                500,
                relationships=RELATIONSHIPS,
                analysis_plan=self.plan,
            )

    def test_guard_rejects_distinct_play_count_semantics(self):
        distinct_sql = (self.sql or "").replace("COUNT(ps.id)", "COUNT(DISTINCT ps.id)")
        with self.assertRaisesRegex(SQLSafetyError, "不允许 DISTINCT"):
            validate_and_normalize_sql(
                distinct_sql,
                TABLE_INFOS,
                500,
                sensitive_columns=extract_sensitive_columns(TABLE_INFOS),
                filter_only_columns=extract_filter_only_columns(TABLE_INFOS),
                relationships=RELATIONSHIPS,
                analysis_plan=self.plan,
            )

    def test_filter_only_region_cannot_be_selected(self):
        with self.assertRaisesRegex(SQLSafetyError, "敏感字段"):
            validate_and_normalize_sql(
                "SELECT up.province, COUNT(*) FROM user_profile up GROUP BY up.province",
                TABLE_INFOS,
                500,
                sensitive_columns=extract_sensitive_columns(TABLE_INFOS),
                filter_only_columns=extract_filter_only_columns(TABLE_INFOS),
            )

    def test_execute_sql_revalidates_filter_only_region(self):
        class WarehouseRepository:
            async def execute_sql(self, sql, timeout_seconds):
                self.sql = sql
                return [{"播放总次数": 6675, "平均播放时长差（秒）": -15.8782}]

        async def run():
            events = []
            warehouse = WarehouseRepository()
            runtime = SimpleNamespace(
                context={"dw_mysql_repository": warehouse, "feedback_learning_service": None},
                stream_writer=events.append,
            )
            result = await execute_sql(
                {
                    "sql": self.sql,
                    "table_infos": TABLE_INFOS,
                    "relationships": RELATIONSHIPS,
                    "row_level_scope": [],
                    "analysis_plan": self.plan,
                    "db_info": {"dialect": "mysql"},
                    "correction_attempts": 0,
                },
                runtime,
            )
            return result, events, warehouse.sql

        result, events, executed_sql = asyncio.run(run())

        self.assertEqual(result["result_rows"][0]["播放总次数"], 6675)
        self.assertIn("up.province LIKE '%北京%'", executed_sql)
        self.assertIn({"type": "progress", "step": "执行SQL", "status": "success"}, events)


if __name__ == "__main__":
    unittest.main()
