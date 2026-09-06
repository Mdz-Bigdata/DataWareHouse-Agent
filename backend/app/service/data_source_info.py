"""Describe the connected engine separately from the provenance of its rows."""
from typing import Any


def describe_data_source(database: Any) -> dict[str, str]:
    engine = str(database.active_db_type).lower()
    physical = database.real_engine is not None
    sample = database.is_sample_data
    fixture = getattr(database, "has_project_fixture", sample)
    business = getattr(database, "has_business_tables", not sample)
    postgres = engine in {"postgres", "postgresql"}
    if not physical:
        label = "演示数仓"
        description = "当前使用内存 SQLite，数据来自项目示例。"
    elif postgres:
        label = "PostgreSQL 数仓"
        if sample:
            description = "已连接持久化 PostgreSQL；初始数据由项目示例数据迁移。"
        elif fixture and business:
            description = ("以下结果来自已连接的 PostgreSQL 业务库；"
                           "另有独立 warehouse schema 保存项目示例的交易与听书数据。")
        else:
            description = "以下结果来自已连接的 PostgreSQL 数据库。"
    else:
        label = "已配置业务数据源"
        description = "以下结果来自已连接的数据库。"
    return {
        "mode": "configured" if physical else "demo",
        "engine": engine,
        "label": label,
        "description": description,
        "data_origin": ("project_fixture" if sample else
                        "business_with_fixture" if fixture and business else "business"),
        "database_identity": database.database_identity,
    }
