"""Initialize a dedicated NanZi MySQL database exactly once.

Run inside the corresponding backend image, before the API starts. Upstream SQL
contains destructive resets; it is never suitable for an unmanaged database or
blind retries. A durable per-file ledger and advisory lock enforce that boundary.
No database connection is opened when this module is imported.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


LEDGER = "_nanzi_integration_migrations"
OWNER = "@owner"
FINALIZE = "@initialize-v1"
VERSION_RE = re.compile(r"^V(\d+(?:\.\d+)*)(?:[-_]).+\.sql$")
USER_TABLES = {"data": "api_users", "agents": "ai_agent_users"}


class BootstrapError(RuntimeError):
    """An actionable, secret-free initialization failure."""


@dataclass(frozen=True)
class Migration:
    name: str
    checksum: str
    statements: tuple[str, ...]


def split_sql(sql: str) -> tuple[str, ...]:
    """Split MySQL statements without splitting strings or comment contents.

    The vendored migrations have no procedures or executable comments. Refuse
    those constructs rather than silently changing their semantics.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        char = sql[i]
        if quote:
            current.append(char)
            if char == "\\" and i + 1 < len(sql):
                i += 1
                current.append(sql[i])
            elif char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 1
                    current.append(sql[i])
                else:
                    quote = None
        elif char in ("'", '"', "`"):
            quote = char
            current.append(char)
        elif char == "#" or (
            sql.startswith("--", i)
            and (i + 2 == len(sql) or sql[i + 2].isspace())
        ):
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
            current.append("\n")
            continue
        elif sql.startswith("/*", i):
            if sql.startswith("/*!", i):
                raise BootstrapError("Executable SQL comments require manual review.")
            end = sql.find("*/", i + 2)
            if end < 0:
                raise BootstrapError("Unterminated SQL comment.")
            i = end + 2
            current.append(" ")
            continue
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
        i += 1
    if quote:
        raise BootstrapError("Unterminated SQL string.")
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    if any(re.match(r"DELIMITER\b", s, re.I) for s in statements):
        raise BootstrapError("DELIMITER migrations require manual review.")
    return tuple(statements)


def migration_files(directory: Path) -> list[Path]:
    """Use numeric components, preserving both independently named V31 files."""
    found: list[tuple[tuple[int, ...], str, Path]] = []
    for path in directory.glob("V*.sql"):
        match = VERSION_RE.fullmatch(path.name)
        if not match:
            raise BootstrapError(f"Unrecognized migration filename: {path.name}")
        found.append((tuple(map(int, match[1].split("."))), path.name, path))
    if not found:
        raise BootstrapError("No numbered SQL migrations found.")
    return [item[2] for item in sorted(found)]


def load_migrations(directory: Path) -> list[Migration]:
    result = []
    for path in migration_files(directory):
        content = path.read_bytes()
        result.append(Migration(path.name, hashlib.sha256(content).hexdigest(),
                                split_sql(content.decode("utf-8-sig"))))
    return result


def is_session_directive(statement: str) -> bool:
    # The database is selected explicitly by the container's MYSQL_DB. Ignore
    # upstream database switches and transaction markers: each ledger write and
    # migration statement must commit independently (MySQL DDL auto-commits).
    return bool(re.fullmatch(
        r"(?:CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?\w+`?"
        r"|USE\s+`?\w+`?|BEGIN|START\s+TRANSACTION|COMMIT)",
        statement, re.I,
    ))


def validate_history(rows: list[tuple[str, str, str]], migrations: list[Migration],
                     platform: str) -> tuple[int, bool]:
    history = {name: (checksum, status) for name, checksum, status in rows}
    if history.pop(OWNER, None) != (platform, "complete"):
        raise BootstrapError("Database integration ownership is missing or mismatched.")
    for name, (_, status) in history.items():
        if status != "complete":
            raise BootstrapError(
                f"Unfinished migration {name}; automatic replay is blocked. "
                "Inspect/repair this dedicated database or restore a backup before retrying."
            )
    finalized = history.pop(FINALIZE, None)
    if finalized not in (None, ("v1", "complete")):
        raise BootstrapError("Initialization version does not match the ledger.")
    known = {migration.name for migration in migrations}
    if set(history) - known:
        raise BootstrapError("Ledger contains migrations missing from this source snapshot.")
    completed = 0
    gap = False
    for migration in migrations:
        recorded = history.get(migration.name)
        if recorded is None:
            gap = True
            continue
        if gap or recorded[0] != migration.checksum:
            raise BootstrapError("Migration history is not an unchanged, ordered prefix.")
        completed += 1
    # This runner initializes pinned snapshots, not production upgrades. Fresh
    # upstream destructive migrations must never run after the app has started.
    if finalized and completed != len(migrations):
        raise BootstrapError("New migrations require a reviewed upgrade, not bootstrap replay.")
    return completed, finalized is not None


def known_redundant_column(cursor: Any, platform: str, migration: Migration,
                           statement: str) -> bool:
    """V3 already creates this JSON column; V3.1 redundantly adds it again."""
    if platform != "agents" or migration.name != "V3.1-add_col_synonyms.sql":
        return False
    expected = ("ALTER TABLE meta_columns ADD COLUMN synonyms JSON "
                "COMMENT '同义词列表 (JSON Array)' AFTER enums")
    if " ".join(statement.split()) != expected:
        raise BootstrapError("Known V3.1 migration changed; review it before initialization.")
    cursor.execute(
        "SELECT DATA_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        ("meta_columns", "synonyms"),
    )
    row = cursor.fetchone()
    if row is None:
        return False
    if row[0].lower() != "json":
        raise BootstrapError("meta_columns.synonyms exists with an incompatible type.")
    return True


def mysql_error_code(error: Exception) -> str:
    code = error.args[0] if error.args else None
    return str(code) if isinstance(code, int) else "unavailable"


def apply_migrations(connection: Any, platform: str, migrations: list[Migration],
                     initialize: Callable[[Any], None]) -> bool:
    """Return True only when this invocation finishes a new initialization.

    The caller supplies an autocommit connection. A running ledger row is durable
    before any migration starts. Any interruption then requires manual review;
    no destructive statement is replayed based on an optimistic retry guess.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT CONCAT('nanzi:', DATABASE())")
        lock_name = cursor.fetchone()[0]
        cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
        if cursor.fetchone()[0] != 1:
            raise BootstrapError("Another initialization holds the database lock.")
        try:
            cursor.execute("SHOW TABLES")
            tables = {row[0] for row in cursor.fetchall()}
            if tables and LEDGER not in tables:
                raise BootstrapError("Refusing nonempty unmanaged database; use a dedicated empty database.")
            if not tables:
                cursor.execute(
                    f"CREATE TABLE `{LEDGER}` (name VARCHAR(255) PRIMARY KEY, "
                    "checksum VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL, "
                    "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) "
                    "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
                )
            cursor.execute(f"SELECT name, checksum, status FROM `{LEDGER}`")
            rows = list(cursor.fetchall())
            if not rows and not (tables - {LEDGER}):
                cursor.execute(
                    f"INSERT INTO `{LEDGER}` (name, checksum, status) VALUES (%s, %s, 'complete')",
                    (OWNER, platform),
                )
                rows = [(OWNER, platform, "complete")]
            completed, finalized = validate_history(rows, migrations, platform)
            if finalized:
                return False
            for migration in migrations[completed:]:
                cursor.execute(
                    f"INSERT INTO `{LEDGER}` (name, checksum, status) VALUES (%s, %s, 'running')",
                    (migration.name, migration.checksum),
                )
                for number, statement in enumerate(migration.statements, 1):
                    if is_session_directive(statement):
                        continue
                    try:
                        if not known_redundant_column(cursor, platform, migration, statement):
                            cursor.execute(statement)
                    except BootstrapError:
                        raise
                    except Exception as error:
                        raise BootstrapError(
                            f"{migration.name} statement {number} failed (MySQL code "
                            f"{mysql_error_code(error)}); replay is blocked. Inspect the database."
                        ) from None
                cursor.execute(
                    f"UPDATE `{LEDGER}` SET status = 'complete' WHERE name = %s", (migration.name,)
                )
            # Record before first-run cleanup/admin creation as well. If this
            # transaction fails, rollback both cleanup and admin creation.
            cursor.execute(
                f"INSERT INTO `{LEDGER}` (name, checksum, status) VALUES (%s, %s, 'running')",
                (FINALIZE, "v1"),
            )
            connection.begin()
            try:
                initialize(cursor)
                cursor.execute(
                    f"UPDATE `{LEDGER}` SET status = 'complete' WHERE name = %s", (FINALIZE,)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return True
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def encode_api_key(api_key: str, encryption_key: str) -> tuple[str, str]:
    # Exact upstream APIKeyManager representation: Fernet token, then an
    # additional URL-safe base64 wrapper. AuthService verifies SHA256 separately.
    from cryptography.fernet import Fernet

    encrypted = Fernet(encryption_key.encode()).encrypt(api_key.encode())
    return base64.urlsafe_b64encode(encrypted).decode(), hashlib.sha256(api_key.encode()).hexdigest()


def create_admin(cursor: Any, platform: str, username: str,
                 encrypted_key: str, key_hash: str) -> bool:
    table = USER_TABLES[platform]
    cursor.execute(f"SELECT id, role, status FROM `{table}` WHERE user_name = %s", (username,))
    existing = cursor.fetchone()
    if existing:
        if existing[1:] != ("admin", 1):
            raise BootstrapError("Requested administrator name belongs to a non-admin or disabled account.")
        return False  # Never reset an existing administrator's key or password.
    cursor.execute(
        f"INSERT INTO `{table}` "
        "(user_name, api_key_encrypted, api_key_hash, role, remark, status) "
        "VALUES (%s, %s, %s, 'admin', %s, 1)",
        (username, encrypted_key, key_hash, "DataWareHouse integrated platform administrator"),
    )
    return True


def initialize_defaults(cursor: Any, platform: str, environment: Mapping[str, str],
                        encrypted_key: str, key_hash: str) -> None:
    """Only runs before the first successful bootstrap, never on an app restart."""
    if platform == "data":
        cursor.execute("UPDATE sys_data_source SET status = 0, password = ''")
        cursor.execute("UPDATE sys_resource_meta SET status = 0 WHERE resource_group <> 'System'")
        cursor.execute("UPDATE sys_config SET config_value = '' WHERE config_key LIKE %s", ("ai.%api_key",))
    else:
        cursor.execute("UPDATE ai_models SET is_active = 0, api_key = '', api_base_url = ''")
        cursor.execute("UPDATE sys_api_tools SET is_active = 0, headers = NULL")
        cursor.execute("UPDATE system_configs SET value = '' WHERE is_secret = 1")
        cursor.execute(
            "UPDATE system_configs SET value = '' WHERE `key` IN "
            "('embed_api_url', 'ragflow_api_url', 'knowledge_ragflow_api_url', 'llm_model_name')"
        )
        config = {
            "external_sql_api_url": ("http://data-api:8000/api/v1/sql/execute", 0),
            "external_sql_api_key": (environment["DATA_PLATFORM_API_KEY"], 1),
            "external_sql_data_source": (environment.get("DATA_PLATFORM_DATA_SOURCE", ""), 0),
            "sql_execution_mode": ("remote", 0),
            "yovole_sso_enabled": ("false", 0),
        }
        for key, (value, secret) in config.items():
            cursor.execute(
                "INSERT INTO system_configs (`key`, `value`, category, is_secret) "
                "VALUES (%s, %s, 'data_api', %s) ON DUPLICATE KEY UPDATE "
                "`value` = VALUES(`value`), is_secret = VALUES(is_secret)",
                (key, value, secret),
            )
    create_admin(cursor, platform, environment.get("PLATFORM_ADMIN_USERNAME", "admin"), encrypted_key, key_hash)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=tuple(USER_TABLES), required=True)
    parser.add_argument("--migrations", type=Path, default=Path("/app/db-prod"))
    args = parser.parse_args(argv)
    environment = os.environ
    required = ["MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB",
                "ENCRYPTION_KEY", "PLATFORM_ADMIN_API_KEY"]
    if args.platform == "agents":
        required.append("DATA_PLATFORM_API_KEY")
    missing = [key for key in required if not environment.get(key)]
    if missing:
        print("Initialization requires environment variables: " + ", ".join(missing), file=sys.stderr)
        return 1
    connection = None
    try:
        migrations = load_migrations(args.migrations)
        encrypted, key_hash = encode_api_key(environment["PLATFORM_ADMIN_API_KEY"], environment["ENCRYPTION_KEY"])
        import pymysql

        connection = pymysql.connect(
            host=environment["MYSQL_HOST"], port=int(environment.get("MYSQL_PORT", "3306")),
            user=environment["MYSQL_USER"], password=environment["MYSQL_PASSWORD"],
            database=environment["MYSQL_DB"], charset="utf8mb4", autocommit=True,
            connect_timeout=15,
        )
        initialized = apply_migrations(
            connection, args.platform, migrations,
            lambda cursor: initialize_defaults(cursor, args.platform, environment, encrypted, key_hash),
        )
        print(f"NanZi {args.platform}: " + ("initialized; credentials remain in the local environment file."
                                            if initialized else "already initialized; settings preserved."))
        return 0
    except BootstrapError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        # Connection/library errors can contain passwords, SQL, or API keys.
        print(f"Initialization failed ({type(error).__name__}, MySQL code {mysql_error_code(error)}). "
              "Check the environment and database state; secrets are not logged.", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
