"""Offline safety and migration-contract checks; never contact MySQL."""

import base64
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from integrations.nanzi import bootstrap


ROOT = Path(__file__).resolve().parents[2]


class FakeConnection:
    """Small ledger simulator, not a SQL/database compatibility substitute."""

    def __init__(self, tables=(), history=(), fail_statement=None):
        self.tables = set(tables)
        self.history = {name: (checksum, status) for name, checksum, status in history}
        self.executed = []
        self.fail_statement = fail_statement
        self.result = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        self.result = []
        if sql == self.fail_statement:
            raise RuntimeError(1064, "SECRET SQL CONTENT")
        if sql.startswith("SELECT CONCAT"):
            self.result = [("nanzi:test_db",)]
        elif sql.startswith("SELECT GET_LOCK"):
            self.result = [(1,)]
        elif sql == "SHOW TABLES":
            self.result = [(table,) for table in self.tables]
        elif sql.startswith(f"CREATE TABLE `{bootstrap.LEDGER}`"):
            self.tables.add(bootstrap.LEDGER)
        elif sql.startswith(f"SELECT name, checksum, status FROM `{bootstrap.LEDGER}`"):
            self.result = [(name, *record) for name, record in self.history.items()]
        elif sql.startswith(f"INSERT INTO `{bootstrap.LEDGER}`"):
            status = "running" if "'running'" in sql else "complete"
            self.history[params[0]] = (params[1], status)
        elif sql.startswith(f"UPDATE `{bootstrap.LEDGER}`"):
            self.history[params[0]] = (self.history[params[0]][0], "complete")

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def begin(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class SqlParserTests(unittest.TestCase):
    def test_strings_comments_and_escaped_quotes(self):
        sql = "-- don't split ' ;\nSELECT 'a;b', 'it''s', `a;b`; # ' ;\n SELECT 'a\\\'b'; /* '; */"
        self.assertEqual(bootstrap.split_sql(sql), ("SELECT 'a;b', 'it''s', `a;b`", "SELECT 'a\\\'b'"))

    def test_preserves_comment_like_content_inside_prompt(self):
        self.assertEqual(bootstrap.split_sql("INSERT INTO prompts VALUES ('-- code; /* x */ # text');"),
                         ("INSERT INTO prompts VALUES ('-- code; /* x */ # text')",))

    def test_rejects_unsupported_or_incomplete_sql(self):
        for sql in ("SELECT 'abc", "/* incomplete", "/*! DROP TABLE users */;", "DELIMITER $$\nSELECT 1"):
            with self.subTest(sql=sql), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.split_sql(sql)

    def test_numeric_sort_includes_decimal_and_duplicate_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            names = ["V10-ten.sql", "V3.1-extra.sql", "V31-b.sql", "V3-three.sql", "V31-a.sql", "V0-zero.sql"]
            for name in names:
                (directory / name).write_text("SELECT 1;", encoding="utf-8")
            self.assertEqual([path.name for path in bootstrap.migration_files(directory)],
                             ["V0-zero.sql", "V3-three.sql", "V3.1-extra.sql", "V10-ten.sql", "V31-a.sql", "V31-b.sql"])

    def test_all_vendored_migrations_parse_without_database_switches(self):
        for platform, directory in (("data", "nanzi-api-data-platform"), ("agents", "nanzi-ai-agent-platform")):
            with self.subTest(platform=platform):
                migrations = bootstrap.load_migrations(ROOT / "apps" / directory / "db-prod")
                self.assertGreater(len(migrations), 35)
                for migration in migrations:
                    for statement in migration.statements:
                        if statement.upper().startswith(("USE ", "CREATE DATABASE ")):
                            self.assertTrue(bootstrap.is_session_directive(statement))
                self.assertTrue(migrations[0].name.startswith("V0-"))

    def test_drops_are_not_misclassified_as_harmless_directives(self):
        self.assertTrue(bootstrap.is_session_directive("BEGIN"))
        self.assertFalse(bootstrap.is_session_directive("DROP TABLE api_users"))
        self.assertFalse(bootstrap.is_session_directive("CREATE TABLE api_users (id INT)"))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.migrations = [bootstrap.Migration("V0-init.sql", "abc", ("DROP TABLE IF EXISTS business", "CREATE TABLE business (id INT)")),
                           bootstrap.Migration("V2-next.sql", "def", ("ALTER TABLE business ADD name TEXT",))]
        self.owner = (bootstrap.OWNER, "data", "complete")

    def test_fresh_then_repeat_never_replays_destructive_sql_or_initialization(self):
        connection = FakeConnection()
        initialize = MagicMock()
        self.assertTrue(bootstrap.apply_migrations(connection, "data", self.migrations, initialize))
        self.assertFalse(bootstrap.apply_migrations(connection, "data", self.migrations, initialize))
        self.assertEqual([sql for sql, _ in connection.executed].count("DROP TABLE IF EXISTS business"), 1)
        initialize.assert_called_once()
        self.assertEqual(connection.commits, 1)

    def test_refuses_nonempty_unmanaged_database_before_mutation(self):
        connection = FakeConnection(tables=("api_users",))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "nonempty unmanaged"):
            bootstrap.apply_migrations(connection, "data", self.migrations, MagicMock())
        self.assertFalse(any(sql.startswith(("CREATE", "DROP", "INSERT", "UPDATE")) for sql, _ in connection.executed))

    def test_resumes_only_completed_prefix(self):
        connection = FakeConnection((bootstrap.LEDGER, "business"), (self.owner, ("V0-init.sql", "abc", "complete")))
        self.assertTrue(bootstrap.apply_migrations(connection, "data", self.migrations, MagicMock()))
        sqls = [sql for sql, _ in connection.executed]
        self.assertNotIn("DROP TABLE IF EXISTS business", sqls)
        self.assertIn("ALTER TABLE business ADD name TEXT", sqls)

    def test_failed_ddl_is_durable_and_retry_refuses_to_replay(self):
        connection = FakeConnection(fail_statement="CREATE TABLE business (id INT)")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "V0-init.sql statement 2") as caught:
            bootstrap.apply_migrations(connection, "data", self.migrations, MagicMock())
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(connection.history["V0-init.sql"], ("abc", "running"))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "automatic replay is blocked"):
            bootstrap.apply_migrations(connection, "data", self.migrations, MagicMock())
        self.assertEqual([sql for sql, _ in connection.executed].count("DROP TABLE IF EXISTS business"), 1)

    def test_failure_during_finalize_rolls_back_and_blocks_retry(self):
        connection = FakeConnection()
        with self.assertRaises(RuntimeError):
            bootstrap.apply_migrations(connection, "data", self.migrations, MagicMock(side_effect=RuntimeError("fail")))
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.history[bootstrap.FINALIZE], ("v1", "running"))

    def test_rejects_checksum_changes_gaps_unknown_files_and_wrong_platform(self):
        invalid = [
            [self.owner, ("V0-init.sql", "changed", "complete")],
            [self.owner, ("V2-next.sql", "def", "complete")],
            [self.owner, ("V99-removed.sql", "x", "complete")],
            [(bootstrap.OWNER, "agents", "complete")],
            [self.owner, ("V0-init.sql", "abc", "running")],
        ]
        for history in invalid:
            with self.subTest(history=history), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.validate_history(history, self.migrations, "data")

    def test_finalized_snapshot_refuses_new_migrations(self):
        history = [self.owner, ("V0-init.sql", "abc", "complete"), (bootstrap.FINALIZE, "v1", "complete")]
        with self.assertRaisesRegex(bootstrap.BootstrapError, "reviewed upgrade"):
            bootstrap.validate_history(history, self.migrations, "data")

    def test_advisory_lock_is_released_after_failure(self):
        connection = FakeConnection(tables=("business",))
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.apply_migrations(connection, "data", self.migrations, MagicMock())
        self.assertEqual(connection.executed[-1][0], "SELECT RELEASE_LOCK(%s)")


class InitializationTests(unittest.TestCase):
    def test_known_duplicate_requires_correct_column_type(self):
        path = ROOT / "apps/nanzi-ai-agent-platform/db-prod/V3.1-add_col_synonyms.sql"
        statement = bootstrap.split_sql(path.read_text())[0]
        migration = bootstrap.Migration(path.name, "", (statement,))
        cursor = MagicMock()
        cursor.fetchone.return_value = ("json",)
        self.assertTrue(bootstrap.known_redundant_column(cursor, "agents", migration, statement))
        cursor.fetchone.return_value = ("text",)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.known_redundant_column(cursor, "agents", migration, statement)

    def test_arbitrary_duplicate_errors_are_not_suppressed(self):
        migration = bootstrap.Migration("V8-other.sql", "", ("ALTER TABLE x ADD COLUMN y INT",))
        self.assertFalse(bootstrap.known_redundant_column(MagicMock(), "agents", migration, migration.statements[0]))

    def test_admin_uses_encrypted_hash_format_and_parameterized_insert(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        self.assertTrue(bootstrap.create_admin(cursor, "data", "admin", "encrypted", "hash"))
        sql, params = cursor.execute.call_args.args
        self.assertIn("INSERT INTO `api_users`", sql)
        self.assertEqual(params[:3], ("admin", "encrypted", "hash"))
        self.assertNotIn("encrypted", sql.replace("api_key_encrypted", ""))

    def test_admin_is_preserved_without_rotating_existing_credentials(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1, "admin", 1)
        self.assertFalse(bootstrap.create_admin(cursor, "agents", "admin", "new-encrypted", "new-hash"))
        self.assertEqual(cursor.execute.call_count, 1)

    def test_existing_nonadmin_is_not_elevated(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1, "user", 1)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.create_admin(cursor, "agents", "admin", "new", "new")

    def test_agent_internal_sql_connection_and_seed_sanitization(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        bootstrap.initialize_defaults(cursor, "agents", {"DATA_PLATFORM_API_KEY": "generated-secret"}, "encrypted", "hash")
        calls = [call.args for call in cursor.execute.call_args_list]
        values = [args[1] for args in calls if len(args) == 2]
        self.assertIn(("external_sql_api_url", "http://data-api:8000/api/v1/sql/execute", 0), values)
        self.assertIn(("external_sql_api_key", "generated-secret", 1), values)
        self.assertIn(("sql_execution_mode", "remote", 0), values)
        self.assertTrue(any("UPDATE ai_models SET is_active = 0" in args[0] for args in calls))
        self.assertFalse(any("generated-secret" in args[0] for args in calls))

    def test_missing_environment_fails_without_connecting(self):
        output = StringIO()
        with patch.dict("os.environ", {}, clear=True), redirect_stderr(output):
            self.assertEqual(bootstrap.main(["--platform", "agents"]), 1)
        self.assertIn("DATA_PLATFORM_API_KEY", output.getvalue())

    def test_key_representation_matches_upstream_double_base64(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography is supplied by the backend image")
        key = Fernet.generate_key()
        encrypted, digest = bootstrap.encode_api_key("test-api-key", key.decode())
        self.assertEqual(Fernet(key).decrypt(base64.urlsafe_b64decode(encrypted)), b"test-api-key")
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
