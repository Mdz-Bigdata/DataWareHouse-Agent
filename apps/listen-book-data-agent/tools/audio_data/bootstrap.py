"""Create the audiobook schema and optionally populate deterministic demo data."""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

import pymysql

from .generate.config import DB_CONFIG, ROOT_DIR

LOGGER = logging.getLogger(__name__)
EXPECTED_TABLE_COUNT = 54
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely initialize the audiobook analytics database."
    )
    parser.add_argument(
        "--profile",
        choices=("smoke", "full"),
        default="smoke",
        help="deterministic data volume to generate (default: smoke)",
    )
    parser.add_argument(
        "--schema",
        default=str(DB_CONFIG["database"]),
        help="target schema name (default: AUDIO_DB_NAME/DB_NAME/audio)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="explicitly allow dropping an existing target schema",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="create the 54 tables without generating rows",
    )
    return parser.parse_args()


def _admin_config() -> dict[str, object]:
    return {
        key: value
        for key, value in DB_CONFIG.items()
        if key not in {"database", "autocommit"}
    }


def _schema_exists(schema: str) -> bool:
    with pymysql.connect(**_admin_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (schema,),
            )
            return cursor.fetchone() is not None


def _execute_ddl(schema: str, ddl_path: Path) -> None:
    ddl = ddl_path.read_text(encoding="utf-8")
    table_count = len(re.findall(r"(?im)^\s*CREATE\s+TABLE\s+", ddl))
    if table_count != EXPECTED_TABLE_COUNT:
        raise RuntimeError(
            f"DDL table count mismatch: expected {EXPECTED_TABLE_COUNT}, got {table_count}"
        )

    connection = pymysql.connect(**_admin_config(), database=schema, autocommit=True)
    try:
        with connection.cursor() as cursor:
            for statement in (part.strip() for part in ddl.split(";")):
                if statement:
                    cursor.execute(statement)
    finally:
        connection.close()


def initialize_schema(schema: str, *, reset: bool) -> None:
    if not IDENTIFIER_PATTERN.fullmatch(schema):
        raise ValueError("schema must contain only letters, digits, and underscores")

    exists = _schema_exists(schema)
    if exists and not reset:
        raise RuntimeError(
            f"schema {schema!r} already exists; rerun with --reset to rebuild it"
        )

    connection = pymysql.connect(**_admin_config(), autocommit=True)
    try:
        with connection.cursor() as cursor:
            if exists:
                LOGGER.warning("Dropping existing schema %s because --reset was supplied", schema)
                cursor.execute(f"DROP DATABASE `{schema}`")
            cursor.execute(
                f"CREATE DATABASE `{schema}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
    finally:
        connection.close()

    _execute_ddl(schema, ROOT_DIR / "sql" / "audio.sql")
    LOGGER.info("Created schema %s with %s tables", schema, EXPECTED_TABLE_COUNT)


def generate_data(schema: str, profile: str) -> None:
    DB_CONFIG["database"] = schema

    from .generate.config import GENERATION_DEFAULTS, generation_profile
    from .generate.db import close_db, init_db, interrupt_db
    from .generate.main import run_acceptance, run_generators
    from .generate.progress import console_print, progress_context

    interrupted = False
    init_db()
    try:
        with generation_profile(profile):
            with progress_context():
                console_print(f"Generation profile: {profile} -> {GENERATION_DEFAULTS}")
                run_generators()
                run_acceptance()
    except KeyboardInterrupt:
        interrupted = True
        interrupt_db()
        raise SystemExit(130) from None
    finally:
        if not interrupted:
            close_db()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    initialize_schema(args.schema, reset=args.reset)
    if not args.schema_only:
        generate_data(args.schema, args.profile)


if __name__ == "__main__":
    main()
