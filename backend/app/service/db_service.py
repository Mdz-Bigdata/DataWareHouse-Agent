# -*- coding: utf-8 -*-
import os
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import timedelta
import sqlglot
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateTable
import hashlib
from dotenv import load_dotenv

# 自动加载当前目录或上层目录中的 .env 配置文件
load_dotenv()

# Queries always use the selected database. SQLite source fixtures are only created
# in SQLite mode; managed PostgreSQL stores the same initial rows persistently.

class DBService:
    def __init__(self, source=None):
        self.conn = None
        self.real_engine = None
        self.active_db_type = "sqlite"
        self._has_project_fixture = False
        self._has_business_tables = False
        # Schemas searched for metadata, in the same precedence the database uses
        # to resolve unqualified table names.
        self.query_schemas: list[str] = []
        # A selected source is authoritative; otherwise the process environment decides.
        self.source_config = source
        if source is not None:
            if source.engine != "sqlite":
                self._connect(source.url, source.engine, source.pool_size, source.max_overflow)
        elif os.getenv("DB_TYPE") != "sqlite":
            self._setup_real_database_connection()
        if self.real_engine is None:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._register_sqlite_udfs()
            self._initialize_mock_data()

    @property
    def has_project_fixture(self) -> bool:
        """The destination carries this project's migrated demonstration schema."""
        return getattr(self, "_has_project_fixture", False)

    @property
    def has_business_tables(self) -> bool:
        """The destination carries relations this project did not create."""
        return getattr(self, "_has_business_tables", False)

    @property
    def is_sample_data(self) -> bool:
        """True only when every queryable row came from the project fixture."""
        if self.real_engine is None:
            return self.active_db_type == "sqlite"
        return self.has_project_fixture and not self.has_business_tables

    @property
    def database_identity(self) -> str:
        """Stable destination identity; passwords and other URL secrets are excluded."""
        if self.real_engine is None:
            return "sqlite:memory"
        url = self.real_engine.url
        driver = url.get_backend_name()
        port = url.port or {"postgresql": 5432, "mysql": 3306}.get(driver, "")
        identity = f"{driver}|{url.host or ''}|{port}|{url.database or ''}|{url.username or ''}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @property
    def source_id(self) -> str:
        return self.database_identity

    def _register_sqlite_udfs(self):
        """
        注册一些 ClickHouse / Doris 中常用的函数到 SQLite，以防止转译后的边缘 case 执行报错。
        """
        # 模拟 toStartOfMonth/date_trunc 等
        def to_start_of_month(date_str):
            try:
                dt = pd.to_datetime(date_str)
                return dt.strftime("%Y-%m-01")
            except Exception:
                return date_str

        def date_sub(*args):
            if not args:
                return ""
            date_str = args[0]
            days = 0
            for val in args[1:]:
                try:
                    days = int(val)
                    break
                except Exception:
                    if isinstance(val, str):
                        import re
                        num = re.findall(r"\d+", val)
                        if num:
                            days = int(num[0])
                            break
            try:
                dt = pd.to_datetime(date_str)
                return (dt - timedelta(days=days)).strftime("%Y-%m-%d")
            except Exception:
                return date_str

        self.conn.create_function("toStartOfMonth", 1, to_start_of_month)
        self.conn.create_function("to_start_of_month", 1, to_start_of_month)
        self.conn.create_function("date_trunc", 2, lambda unit, dt: to_start_of_month(dt) if 'month' in unit.lower() else dt)
        self.conn.create_function("date_sub", -1, date_sub)
        self.conn.create_function("toIntervalDay", 1, lambda days: int(days))

    def _initialize_mock_data(self):
        """Initialize the shared fixture only for explicitly selected SQLite mode."""
        from app.service.warehouse_fixture import initialize_fixture
        initialize_fixture(self.conn)

    def _setup_real_database_connection(self):
        """
        动态加载物理数据库连接。
        优先从系统环境变量中提取连接参数（支持 .env 方式），其次回退读取 backend/llm_config.json。
        """
        # 1. 优先尝试从环境变量读取 (.env 方式)，兼容 DATABASE_URL 与 DB_URL
        db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
        db_type = os.getenv("DB_TYPE")
        pool_size_env = os.getenv("DB_POOL_SIZE")
        max_overflow_env = os.getenv("DB_MAX_OVERFLOW")
        
        # 自动将异步连接协议重写为同步连接协议，以支持 pandas 和 SQLAlchemy 同步连接池
        if db_url:
            from app.service.warehouse_migration import sync_database_url
            db_url = sync_database_url(db_url)
        
        pool_size = int(pool_size_env) if pool_size_env else 10
        max_overflow = int(max_overflow_env) if max_overflow_env else 20
        
        # 2. 如果环境变量没有提供 DB_URL / DATABASE_URL，则回退读取 llm_config.json
        if not db_url:
            config_path = Path(__file__).resolve().parents[2] / "llm_config.json"
            if os.path.exists(config_path):
                try:
                    import json
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    db_cfg = config_data.get("database")
                    if db_cfg:
                        active_db = db_cfg.get("active_db", "sqlite")
                        if active_db != "sqlite":
                            conn_info = db_cfg.get("connections", {}).get(active_db, {})
                            db_url = conn_info.get("url")
                            db_type = active_db
                            pool_size = conn_info.get("pool_size", pool_size)
                            max_overflow = conn_info.get("max_overflow", max_overflow)
                except Exception:
                    raise RuntimeError("无法读取业务数据源配置，请检查 llm_config.json 格式。") from None
                    
        # 3. 如果成功获取连接串，初始化连接池
        if db_url:
            self._connect(db_url, db_type, pool_size, max_overflow)
        elif db_type and db_type.lower() != "sqlite":
            raise RuntimeError("已选择业务数据源，但没有配置数据库连接地址；未切换到演示数仓。")

    def _connect(self, db_url: str, db_type: str | None, pool_size: int = 10, max_overflow: int = 20):
        """Open and verify one physical connection pool; never fall back to demo data."""
        from app.service.data_sources import engine_from_url, normalize_engine
        from app.service.warehouse_migration import sync_database_url
        if not db_url:
            raise RuntimeError("已选择业务数据源，但没有配置数据库连接地址；未切换到演示数仓。")
        db_url = sync_database_url(db_url)
        # Doris and StarRocks speak MySQL's wire protocol, so only the declared
        # type distinguishes them; the URL scheme is the fallback.
        engine_name = normalize_engine(db_type) or engine_from_url(db_url) or "mysql"
        try:
            connect_args = {}
            if engine_name in {"postgresql", "mysql", "doris", "starrocks"}:
                connect_args["connect_timeout"] = 2
            if engine_name == "postgresql":
                # Business relations keep priority over the demonstration schema,
                # so unqualified names resolve exactly as metadata discovery lists them.
                from app.service.warehouse_migration import FIXTURE_SCHEMA
                connect_args["options"] = f"-c search_path=public,{FIXTURE_SCHEMA}"
            pooling = {} if engine_name in {"duckdb", "sqlite"} else {
                "pool_size": pool_size, "max_overflow": max_overflow}
            self.real_engine = create_engine(
                db_url, pool_pre_ping=True, connect_args=connect_args, **pooling)
            self.active_db_type = engine_name
            with self.real_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if self.real_engine.dialect.name == "postgresql":
                    self._inspect_postgres_layout(connection)
            print(f"[DBService] 物理数据源连接已验证！类型: {self.active_db_type.upper()}")
        except Exception:
            if self.real_engine is not None:
                self.real_engine.dispose()
                self.real_engine = None
            raise RuntimeError("业务数据源初始化失败，请检查连接配置及数据库驱动；未切换到演示数仓。") from None

    def _inspect_postgres_layout(self, connection) -> None:
        """Record which schemas answer queries and where their rows came from."""
        from app.service.warehouse_migration import FIXTURE_SCHEMA, MARKER_SCHEMA, is_project_fixture
        self._has_project_fixture = is_project_fixture(connection)
        inspector = inspect(connection)
        available = set(inspector.get_schema_names())
        self.query_schemas = [schema for schema in ("public", FIXTURE_SCHEMA) if schema in available]
        self._has_business_tables = any(
            inspector.get_table_names(schema=schema) or inspector.get_view_names(schema=schema)
            for schema in available
            if schema not in {FIXTURE_SCHEMA, MARKER_SCHEMA, "information_schema"}
            and not schema.startswith("pg_")
        )

    def _execute_sqlite(self, sql: str, dialect: str = "doris") -> pd.DataFrame:
        """
        在数仓湖仓引擎 (SQLite 仿真底座) 上转译并执行查询
        """
        try:
            translated_sqls = sqlglot.transpile(sql, read=dialect, write="sqlite")
            sqlite_sql = translated_sqls[0]
            db_name = self.get_active_db_name()
            if db_name:
                sqlite_sql = sqlite_sql.replace(f"{db_name}.", "")
            import re
            sqlite_sql = re.sub(r"TIMESTAMP_TRUNC\(([^,]+),\s*MONTH\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
            sqlite_sql = re.sub(r"date_trunc\('month',\s*([^)]+)\)", r"strftime('%Y-%m-01', \1)", sqlite_sql, flags=re.IGNORECASE)
        except Exception:
            sqlite_sql = sql
            db_name = self.get_active_db_name()
            if db_name:
                sqlite_sql = sqlite_sql.replace(f"{db_name}.", "")

        print(f"Executing query on Lakehouse Engine (SQLite):\n--- Source ({dialect}) ---\n{sql}\n--- Target (sqlite) ---\n{sqlite_sql}")
        df = pd.read_sql_query(sqlite_sql, self.conn)
        return df

    def execute_query(self, sql: str, dialect: str = "mysql") -> pd.DataFrame:
        """
        在当前数据源执行查询。物理库错误交由调用方处理，不能用演示数据替代。
        未启用物理连接时，使用本地 SQLite 演示数据源。
        """
        sql = sql.strip().rstrip(";")
        if self.real_engine is None:
            return self._execute_sqlite(sql, dialect)

        from app.service.data_sources import normalize_engine, sql_dialect
        target_dialect = sql_dialect(normalize_engine(self.active_db_type))

        target_sql = sqlglot.transpile(sql, read=dialect, write=target_dialect)[0]

        # PostgreSQL 使用 schema.table，移除当前数据库名而保留 schema。
        if target_dialect == "postgres":
            db_name = self.get_active_db_name()
            if db_name:
                query = sqlglot.parse_one(target_sql, read=target_dialect)
                for table in query.find_all(sqlglot.exp.Table):
                    if table.catalog == db_name:
                        table.set("catalog", None)
                    elif table.db == db_name:
                        table.set("db", None)
                target_sql = query.sql(dialect=target_dialect)

        print(f"Executing query on {self.active_db_type.upper()}:\n--- Source ({dialect}) ---\n{sql}\n--- Target ({self.active_db_type}) ---\n{target_sql}")
        with self.real_engine.connect() as connection:
            return pd.read_sql_query(target_sql, connection)

    def get_active_db_name(self) -> str:
        """Return the database actually connected, never a stale .env fallback."""
        if self.real_engine is None:
            return ""
        return self.real_engine.url.database or ""

    def get_table_schema(self, table_name: str) -> str:
        """Read DDL from the active source only, honoring schema precedence."""
        if self.real_engine is not None:
            from sqlalchemy import MetaData, Table
            inspector = inspect(self.real_engine)
            schema = next((name for name in self.query_schemas
                           if inspector.has_table(table_name, schema=name)), None)
            if schema is None and not inspector.has_table(table_name):
                return ""
            table = Table(table_name, MetaData(), schema=schema, autoload_with=self.real_engine)
            return str(CreateTable(table).compile(self.real_engine))
        cursor = self.conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        row = cursor.fetchone()
        return row[0] if row else ""

# 单例实例，方便跨 API 和服务共享
db_service = DBService()
