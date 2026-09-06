"""Phase 3.4：数据源管理的 Pydantic 模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DatasourceUpsert(BaseModel):
    """创建/更新数据源请求。"""

    id: str = Field(..., description="数据源标识，如 warehouse_audio")
    name: str = Field(..., description="展示名称")
    dialect: Literal["mysql", "postgresql", "clickhouse", "doris"] = Field(
        ..., description="数据库方言"
    )
    host: str
    port: int = Field(..., ge=1, le=65535)
    database: str
    user: str
    password: str = Field(..., description="明文密码，落库前加密")
    active: bool = False
    description: str = ""


class DatasourceUpdate(BaseModel):
    """部分更新数据源请求（所有字段可选）。"""

    name: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = None
    user: str | None = None
    password: str | None = Field(default=None, description="留空则不修改密码")
    active: bool | None = None
    description: str | None = None


class DatasourceItem(BaseModel):
    """数据源展示项（密码脱敏，绝不明文）。"""

    id: str
    name: str
    dialect: str
    host: str
    port: int
    database: str
    user: str
    password_masked: str  # 脱敏后的密码
    active: bool
    description: str
