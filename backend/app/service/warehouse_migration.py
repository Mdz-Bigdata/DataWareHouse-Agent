"""Transactional, one-time migration of project source rows to PostgreSQL.

The demonstration warehouse always lives in its own schema so that a destination
holding real business tables keeps every existing relation untouched. Only the
dedicated fixture schema and its ownership marker are ever created or inspected.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import sqlite3
from typing import Any

from sqlalchemy import (
    BigInteger, Column, Date, DateTime, Float, MetaData, Numeric, Table, Text,
    create_engine, inspect, text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.types import TypeEngine

from app.service.warehouse_fixture import FIXTURE_TABLES, initialize_fixture

OWNER = "DataWareHouse-Agent/project-fixture"
VERSION = 2
FIXTURE_SCHEMA = "warehouse"
MARKER_SCHEMA = "warehouse_meta"
MARKER_TABLE = "bootstrap"
LOCK_ID = 723784573271621


class WarehouseMigrationError(RuntimeError):
    """A destination cannot be safely initialized or reused."""


def sync_database_url(database_url: str) -> str:
    """Async drivers cannot back SQLAlchemy's synchronous pool or pandas readers."""
    for asynchronous, synchronous in (("postgresql+asyncpg://", "postgresql+psycopg2://"),
                                      ("mysql+aiomysql://", "mysql+pymysql://")):
        if database_url.startswith(asynchronous):
            return synchronous + database_url[len(asynchronous):]
    return database_url


def _marker(connection: Connection) -> dict[str, Any] | None:
    if not inspect(connection).has_table(MARKER_TABLE, schema=MARKER_SCHEMA):
        return None
    rows = connection.execute(text(
        "SELECT owner, version, data_origin, schema_name, fixture_date, row_counts "
        "FROM warehouse_meta.bootstrap WHERE id = 1"
    )).mappings().all()
    return dict(rows[0]) if len(rows) == 1 else None


def is_project_fixture(connection: Connection) -> bool:
    marker = _marker(connection)
    return bool(marker and marker["owner"] == OWNER and marker["version"] == VERSION
                and marker["data_origin"] == "project_fixture"
                and marker["schema_name"] == FIXTURE_SCHEMA)


def source_tables(reference_date: datetime | None = None) -> tuple[MetaData, dict[str, list[dict[str, Any]]]]:
    """Read source data once and assign native PostgreSQL types to its columns."""
    metadata = MetaData()
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    source = sqlite3.connect(":memory:")
    try:
        initialize_fixture(source, reference_date)
        for name in FIXTURE_TABLES:
            columns = []
            converters: dict[str, Any] = {}
            for _, column, source_type, _, _, primary_key in source.execute(f"PRAGMA table_info({name})"):
                target_type: TypeEngine[Any]
                if column == "dt":
                    target_type = Date()
                    converters[column] = date.fromisoformat
                elif column == "created_at":
                    target_type = DateTime()
                    converters[column] = datetime.fromisoformat
                elif column in {"gmv", "refund_amount", "audio_gmv", "audio_refund_amount"}:
                    target_type = Numeric(20, 2)
                    converters[column] = lambda value: Decimal(str(value))
                elif source_type.upper() == "INTEGER":
                    target_type = BigInteger()
                elif source_type.upper() == "REAL":
                    target_type = Float()
                else:
                    target_type = Text()
                columns.append(Column(column, target_type, primary_key=bool(primary_key), autoincrement=False))
            table = Table(name, metadata, *columns, schema=FIXTURE_SCHEMA)
            names = list(table.columns.keys())
            rows_by_table[name] = [
                {column: converters[column](value) if column in converters and value is not None else value
                 for column, value in zip(names, row)}
                for row in source.execute(f"SELECT * FROM {name}").fetchall()
            ]
    finally:
        source.close()
    return metadata, rows_by_table


def _validate_tables(connection: Connection, metadata: MetaData) -> None:
    inspector = inspect(connection)
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name, schema=FIXTURE_SCHEMA):
            raise WarehouseMigrationError(f"已初始化数仓缺少表 {table.name}；不会自动重建或覆盖数据。")
        actual = {column["name"]: column for column in inspector.get_columns(table.name, schema=FIXTURE_SCHEMA)}
        for column in table.columns:
            if column.name not in actual:
                raise WarehouseMigrationError(f"数仓表 {table.name} 缺少字段 {column.name}；请检查数据库迁移状态。")
            actual_type = actual[column.name]["type"]
            if column.type._type_affinity is not actual_type._type_affinity:
                raise WarehouseMigrationError(f"数仓表 {table.name}.{column.name} 类型与迁移记录不兼容。")


def migrate_warehouse(engine: Engine, reference_date: datetime | None = None) -> dict[str, Any]:
    """Seed only an unowned, absent fixture schema; never refresh or replace data."""
    if engine.dialect.name != "postgresql":
        raise WarehouseMigrationError("持久化数仓迁移仅支持 PostgreSQL。")
    reference_date = reference_date or datetime.now()
    metadata, source_rows = source_tables(reference_date)
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": LOCK_ID})
        inspector = inspect(connection)
        marker = _marker(connection)
        if marker:
            if not is_project_fixture(connection):
                raise WarehouseMigrationError("数仓归属或迁移版本不匹配；不会修改现有数据。")
            _validate_tables(connection, metadata)
            return {"status": "preserved", "schema": FIXTURE_SCHEMA,
                    "fixture_date": str(marker["fixture_date"]),
                    "initial_row_counts": json.loads(marker["row_counts"])}

        # Business relations live in other schemas and are never read or altered.
        # Only an absent, unowned fixture schema may be created; a damaged marker
        # or a foreign schema of the same name must never be reseeded.
        existing = set(inspector.get_schema_names())
        conflicting = sorted({FIXTURE_SCHEMA, MARKER_SCHEMA} & existing)
        if conflicting:
            raise WarehouseMigrationError(
                f"目标库已存在 schema {'、'.join(conflicting)} 但没有本项目归属记录；"
                "拒绝导入，现有数据保持不变。")

        connection.execute(text(f"CREATE SCHEMA {FIXTURE_SCHEMA}"))
        connection.execute(text(f"CREATE SCHEMA {MARKER_SCHEMA}"))
        metadata.create_all(connection)
        for table in metadata.sorted_tables:
            if source_rows[table.name]:
                connection.execute(table.insert(), source_rows[table.name])
        connection.execute(text("""
            CREATE TABLE warehouse_meta.bootstrap (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                owner TEXT NOT NULL,
                version INTEGER NOT NULL,
                data_origin TEXT NOT NULL,
                schema_name TEXT NOT NULL,
                fixture_date DATE NOT NULL,
                seeded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                row_counts TEXT NOT NULL
            )
        """))
        row_counts = {name: len(rows) for name, rows in source_rows.items()}
        connection.execute(text("""
            INSERT INTO warehouse_meta.bootstrap
                (id, owner, version, data_origin, schema_name, fixture_date, row_counts)
            VALUES (1, :owner, :version, 'project_fixture', :schema_name, :fixture_date, :row_counts)
        """), {"owner": OWNER, "version": VERSION, "schema_name": FIXTURE_SCHEMA,
               "fixture_date": reference_date.date(),
               "row_counts": json.dumps(row_counts, sort_keys=True)})
        _validate_tables(connection, metadata)
        return {"status": "initialized", "schema": FIXTURE_SCHEMA,
                "fixture_date": str(reference_date.date()), "initial_row_counts": row_counts}


def migrate_url(database_url: str) -> dict[str, Any]:
    engine = create_engine(sync_database_url(database_url), pool_pre_ping=True,
                           connect_args={"connect_timeout": 5})
    try:
        return migrate_warehouse(engine)
    finally:
        engine.dispose()
