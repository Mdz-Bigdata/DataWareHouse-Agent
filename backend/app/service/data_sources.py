"""Registry of the database engines this project can query, and their configuration.

Engines are described independently of whether a connection exists: the UI lists
every supported engine so an operator can see what is available, what is already
configured, and what is still missing, without any engine being silently faked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any
import json
import os

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# The launcher exports the selected source, but a standalone import must still
# see the repository .env the same way DBService does.
load_dotenv()

CONFIG_PATH = Path(__file__).resolve().parents[2] / "llm_config.json"

# Canonical engine, display name, SQL dialect used for translation, and the
# Python driver a connection needs. Doris and StarRocks speak the MySQL protocol.
ENGINES: dict[str, dict[str, str]] = {
    "postgresql": {"label": "PostgreSQL", "dialect": "postgres", "driver": "psycopg2",
                   "example": "postgresql+psycopg2://user:password@host:5432/database"},
    "mysql": {"label": "MySQL", "dialect": "mysql", "driver": "pymysql",
              "example": "mysql+pymysql://user:password@host:3306/database?charset=utf8mb4"},
    "doris": {"label": "Apache Doris", "dialect": "doris", "driver": "pymysql",
              "example": "mysql+pymysql://user:password@host:9030/database"},
    "starrocks": {"label": "StarRocks", "dialect": "starrocks", "driver": "pymysql",
                  "example": "mysql+pymysql://user:password@host:9030/database"},
    "clickhouse": {"label": "ClickHouse", "dialect": "clickhouse", "driver": "clickhouse_connect",
                   "example": "clickhouse+connect://user:password@host:8123/database"},
    "duckdb": {"label": "DuckDB", "dialect": "duckdb", "driver": "duckdb_engine",
               "example": "duckdb:///path/to/warehouse.duckdb"},
    "sqlite": {"label": "SQLite", "dialect": "sqlite", "driver": "sqlite3",
               "example": "内置内存演示数仓，无需配置"},
}

DEMO_SOURCE_ID = "demo-sqlite"

_ALIASES = {"postgres": "postgresql", "psql": "postgresql", "pgsql": "postgresql",
            "mariadb": "mysql", "sqlite3": "sqlite", "duck": "duckdb",
            "click_house": "clickhouse", "star_rocks": "starrocks"}


def normalize_engine(value: str | None) -> str | None:
    """Map a configured type or URL scheme onto a supported engine key."""
    if not value:
        return None
    name = str(value).strip().lower().replace("-", "_")
    name = name.split("+", 1)[0]
    if name in ENGINES:
        return name
    if name in _ALIASES:
        return _ALIASES[name]
    for engine in ENGINES:
        if engine in name:
            return engine
    return None


def engine_from_url(url: str | None) -> str | None:
    """Doris and StarRocks share MySQL's scheme, so the URL alone cannot name them."""
    if not url:
        return None
    scheme = str(url).split("://", 1)[0]
    return normalize_engine(scheme)


def sql_dialect(engine: str | None) -> str:
    return ENGINES.get(engine or "", {}).get("dialect", "mysql")


def driver_installed(engine: str) -> bool:
    driver = ENGINES.get(engine, {}).get("driver", "")
    return bool(driver) and find_spec(driver) is not None


def describe_destination(url: str | None) -> str:
    """Host, port and database only; credentials never reach the API or the UI."""
    if not url:
        return ""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        return ""
    database = (parsed.database or "").split("/")[-1]
    host = parsed.host or "本地文件"
    return f"{host}:{parsed.port}/{database}" if parsed.port else f"{host}/{database}"


@dataclass
class DataSource:
    id: str
    engine: str
    url: str = ""
    origin: str = "config"
    pool_size: int = 10
    max_overflow: int = 20

    @property
    def label(self) -> str:
        return ENGINES.get(self.engine, {}).get("label", self.engine)

    @property
    def dialect(self) -> str:
        return sql_dialect(self.engine)

    @property
    def available(self) -> bool:
        if self.engine == "sqlite":
            return True
        return bool(self.url) and driver_installed(self.engine)

    def public(self, active_id: str | None = None) -> dict[str, Any]:
        engine_info = ENGINES.get(self.engine, {})
        if self.engine == "sqlite" and self.origin == "builtin":
            reason = ""
        elif not self.url:
            reason = f"未配置连接串，可在 llm_config.json 的 database.connections 中添加，例如 {engine_info.get('example', '')}"
        elif not driver_installed(self.engine):
            reason = f"缺少 {engine_info.get('driver', '')} 驱动，请先安装后重启服务"
        else:
            reason = ""
        return {
            "id": self.id, "engine": self.engine, "engine_label": self.label,
            "dialect": self.dialect, "origin": self.origin,
            "destination": describe_destination(self.url),
            "available": self.available, "active": self.id == active_id,
            "unavailable_reason": reason,
        }


def _load_config() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle).get("database") or {}
    except (OSError, ValueError):
        return {}


def configured_sources() -> list[DataSource]:
    """Every supported engine, with the connection configured for it when one exists."""
    found: dict[str, DataSource] = {}

    environment_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL") or ""
    environment_engine = normalize_engine(os.getenv("DB_TYPE")) or engine_from_url(environment_url)
    if environment_engine and environment_engine != "sqlite" and environment_url:
        found[environment_engine] = DataSource(
            id=f"env-{environment_engine}", engine=environment_engine,
            url=environment_url, origin="env")

    config = _load_config()
    for name, connection in (config.get("connections") or {}).items():
        url = (connection or {}).get("url", "")
        engine = normalize_engine(connection.get("type") if connection else None) \
            or normalize_engine(name) or engine_from_url(url)
        if not engine or engine in found:
            continue
        found[engine] = DataSource(
            id=f"config-{name}", engine=engine, url=url, origin="config",
            pool_size=int(connection.get("pool_size", 10)),
            max_overflow=int(connection.get("max_overflow", 20)))

    sources = [DataSource(id=DEMO_SOURCE_ID, engine="sqlite", origin="builtin")]
    for engine in ENGINES:
        if engine == "sqlite":
            continue
        sources.append(found.get(engine) or DataSource(id=f"unset-{engine}", engine=engine, origin="unset"))
    return sources


def find_source(source_id: str) -> DataSource | None:
    return next((source for source in configured_sources() if source.id == source_id), None)
