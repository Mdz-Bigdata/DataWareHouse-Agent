"""Source contract and optional isolated PostgreSQL migration integration tests.

Set WAREHOUSE_TEST_DATABASE_URL to an admin connection to enable integration tests.
Every integration test uses a randomly named temporary database, never the source DB.
"""
from datetime import date, datetime
from decimal import Decimal
import os
import unittest
from unittest.mock import patch
import uuid

from pandas.errors import DatabaseError
from sqlalchemy import Date, Numeric, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.service.warehouse_fixture import FIXTURE_TABLES
from app.service.warehouse_migration import (
    FIXTURE_SCHEMA, WarehouseMigrationError, is_project_fixture, migrate_warehouse,
    source_tables,
)

with patch.dict(os.environ, {"DB_TYPE": "sqlite"}):
    from app.service.db_service import DBService


class FixtureContractTests(unittest.TestCase):
    def test_shared_fixture_uses_dates_money_and_preserves_yesterday_values(self):
        metadata, rows = source_tables(datetime(2026, 9, 5))
        self.assertEqual(set(rows), set(FIXTURE_TABLES))
        self.assertIsInstance(metadata.tables[f"{FIXTURE_SCHEMA}.dws_trade_order_daily"].c.dt.type, Date)
        self.assertIsInstance(metadata.tables[f"{FIXTURE_SCHEMA}.dws_trade_order_daily"].c.gmv.type, Numeric)
        self.assertEqual(len({row["dt"] for row in rows["dws_trade_order_daily"]}), 400)
        yesterday = [row for row in rows["dws_audio_album_daily"] if row["dt"] == date(2026, 9, 4)]
        self.assertEqual(sum(row["play_count"] for row in yesterday), 421000)
        self.assertEqual(rows["dws_trade_order_daily"][0]["refund_amount"], Decimal("3200.0"))

    def test_non_postgres_destination_is_rejected_without_writes(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with self.assertRaisesRegex(WarehouseMigrationError, "仅支持 PostgreSQL"):
            migrate_warehouse(engine)
        self.assertEqual(inspect(engine).get_table_names(), [])

    def test_identity_does_not_include_password_and_normalizes_default_port(self):
        db = DBService.__new__(DBService)
        db.real_engine = create_engine("postgresql://warehouse:first@127.0.0.1/datawarehouse")
        self.addCleanup(db.real_engine.dispose)
        identity = db.database_identity
        db.real_engine = create_engine("postgresql://warehouse:second@127.0.0.1:5432/datawarehouse")
        self.addCleanup(db.real_engine.dispose)
        self.assertEqual(db.database_identity, identity)
        self.assertEqual(len(identity), 64)
        self.assertNotIn("warehouse", identity)


@unittest.skipUnless(os.environ.get("WAREHOUSE_TEST_DATABASE_URL"), "Requires isolated PostgreSQL integration database")
class PostgreSQLMigrationTests(unittest.TestCase):
    def setUp(self):
        # Only generated identifiers are interpolated into database DDL.
        self.database_name = "warehouse_test_" + uuid.uuid4().hex
        admin_url = make_url(os.environ["WAREHOUSE_TEST_DATABASE_URL"])
        self.admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with self.admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{self.database_name}"'))
        self.url = admin_url.set(database=self.database_name)
        self.engine = create_engine(self.url)
        self.addCleanup(self._drop_database)

    def _drop_database(self):
        self.engine.dispose()
        with self.admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{self.database_name}" WITH (FORCE)'))
        self.admin.dispose()

    def test_migration_is_atomic_idempotent_and_preserves_user_changes(self):
        initial = migrate_warehouse(self.engine, datetime(2026, 9, 5))
        self.assertEqual(initial["status"], "initialized")
        self.assertEqual(set(inspect(self.engine).get_table_names(schema=FIXTURE_SCHEMA)), set(FIXTURE_TABLES))
        with self.engine.begin() as connection:
            self.assertTrue(is_project_fixture(connection))
            connection.execute(text(f"UPDATE {FIXTURE_SCHEMA}.dws_trade_order_daily "
                                    "SET refund_amount = 12.34 WHERE dt = '2026-09-04'"))
            connection.execute(text(f"INSERT INTO {FIXTURE_SCHEMA}.dim_goods VALUES ('user_added', '保留用户数据')"))
        repeat = migrate_warehouse(self.engine, datetime(2026, 9, 10))
        self.assertEqual(repeat["status"], "preserved")
        self.assertEqual(repeat["fixture_date"], "2026-09-05")
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text(
                f"SELECT COUNT(*) FROM {FIXTURE_SCHEMA}.dim_goods WHERE goods_id = 'user_added'")), 1)
            self.assertEqual(connection.scalar(text(
                f"SELECT SUM(refund_amount) FROM {FIXTURE_SCHEMA}.dws_trade_order_daily "
                "WHERE dt = '2026-09-04'")), Decimal("49.36"))
            self.assertEqual(connection.scalar(text(
                f"SELECT MAX(dt) FROM {FIXTURE_SCHEMA}.dws_trade_order_daily")), date(2026, 9, 4))

    def test_business_relations_are_left_untouched_beside_the_fixture_schema(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE existing_business (amount NUMERIC)"))
            connection.execute(text("INSERT INTO existing_business VALUES (123.45)"))
        self.assertEqual(migrate_warehouse(self.engine)["status"], "initialized")
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT amount FROM existing_business")), Decimal("123.45"))
            self.assertTrue(is_project_fixture(connection))
        self.assertEqual(inspect(self.engine).get_table_names(schema="public"), ["existing_business"])

    def test_unowned_fixture_schema_is_preserved_and_rejected(self):
        with self.engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {FIXTURE_SCHEMA}"))
            connection.execute(text(f"CREATE TABLE {FIXTURE_SCHEMA}.someone_elses (amount NUMERIC)"))
            connection.execute(text(f"INSERT INTO {FIXTURE_SCHEMA}.someone_elses VALUES (123.45)"))
        with self.assertRaisesRegex(WarehouseMigrationError, "没有本项目归属记录"):
            migrate_warehouse(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(
                text(f"SELECT amount FROM {FIXTURE_SCHEMA}.someone_elses")), Decimal("123.45"))
            self.assertFalse(is_project_fixture(connection))
        self.assertEqual(inspect(self.engine).get_table_names(schema=FIXTURE_SCHEMA), ["someone_elses"])

    def test_failure_rolls_back_tables_rows_and_marker(self):
        with patch("app.service.warehouse_migration._validate_tables", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                migrate_warehouse(self.engine)
        self.assertNotIn(FIXTURE_SCHEMA, inspect(self.engine).get_schema_names())
        self.assertNotIn("warehouse_meta", inspect(self.engine).get_schema_names())
        self.assertEqual(migrate_warehouse(self.engine)["status"], "initialized")

    def test_missing_owned_table_is_not_silently_reseeded(self):
        migrate_warehouse(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text(f"DROP TABLE {FIXTURE_SCHEMA}.dws_trade_order_daily"))
        with self.assertRaisesRegex(WarehouseMigrationError, "缺少表"):
            migrate_warehouse(self.engine)
        self.assertNotIn("dws_trade_order_daily",
                         inspect(self.engine).get_table_names(schema=FIXTURE_SCHEMA))

    def test_dbservice_uses_postgres_only_and_preserves_fixture_origin(self):
        migrate_warehouse(self.engine, datetime(2026, 9, 5))
        with patch.dict(os.environ, {"DB_TYPE": "postgresql", "DATABASE_URL": self.url.render_as_string(hide_password=False)}):
            db = DBService()
        self.addCleanup(db.real_engine.dispose)
        self.assertIsNone(db.conn)
        self.assertTrue(db.is_sample_data)
        self.assertEqual(db.active_db_type, "postgresql")
        result = db.execute_query("SELECT SUM(play_count) AS plays FROM dws_audio_album_daily WHERE dt = '2026-09-04'", "doris")
        self.assertEqual(result.iloc[0]["plays"], 421000)
        with self.engine.begin() as connection:
            connection.execute(text(f"DROP TABLE {FIXTURE_SCHEMA}.dws_audio_album_daily"))
        with self.assertRaises((SQLAlchemyError, DatabaseError)):
            db.execute_query("SELECT play_count FROM dws_audio_album_daily", "postgres")

    def test_unmanaged_postgres_is_business_source(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE live_orders (amount NUMERIC)"))
        with patch.dict(os.environ, {"DB_TYPE": "postgresql", "DATABASE_URL": self.url.render_as_string(hide_password=False)}):
            db = DBService()
        self.addCleanup(db.real_engine.dispose)
        self.assertFalse(db.is_sample_data)
        self.assertIsNone(db.conn)


if __name__ == "__main__":
    unittest.main()
