"""Database engine and sample-data provenance must remain separate in API labels."""
import unittest
from types import SimpleNamespace

from app.service.data_source_info import describe_data_source


class DataSourceInfoTests(unittest.TestCase):
    def source(self, physical, sample, engine):
        return describe_data_source(SimpleNamespace(
            active_db_type=engine, real_engine=object() if physical else None,
            is_sample_data=sample, database_identity="safe-fingerprint",
        ))

    def test_migrated_fixture_is_a_postgres_database_with_sample_provenance(self):
        source = self.source(True, True, "postgresql")
        self.assertEqual(source["mode"], "configured")
        self.assertEqual(source["label"], "PostgreSQL 数仓")
        self.assertEqual(source["data_origin"], "project_fixture")
        self.assertIn("迁移", source["description"])
        self.assertNotIn("业务数据", source["description"])

    def test_existing_business_database_is_not_labeled_as_fixture(self):
        source = self.source(True, False, "postgres")
        self.assertEqual(source["data_origin"], "business")
        self.assertNotIn("示例", source["description"])

    def test_explicit_sqlite_remains_identified_as_in_memory(self):
        source = self.source(False, True, "sqlite")
        self.assertEqual(source["mode"], "demo")
        self.assertIn("内存", source["description"])


if __name__ == "__main__":
    unittest.main()
