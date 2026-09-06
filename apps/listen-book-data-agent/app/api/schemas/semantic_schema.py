"""语义层管理的 Pydantic 模型。所有读写都限定在当前活跃 knowledge build。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DatasourceInfo(BaseModel):
    """数据源展示信息：不含密码。"""

    key: str
    label: str
    host: str
    port: int
    database: str
    user: str


class SemanticOverview(BaseModel):
    active_build_id: str | None
    build_created_at: datetime | None
    tables: int
    columns: int
    metrics: int
    relationships: int
    datasources: list[DatasourceInfo]


class DatasourceTestRequest(BaseModel):
    target: Literal["meta", "warehouse"]


class DatasourceTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None


class SemanticTableItem(BaseModel):
    id: str
    name: str
    role: str
    description: str
    alias: list[str]
    domain: str


class SemanticTableUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    alias: list[str] | None = None
    role: str | None = Field(default=None, min_length=1, max_length=32)


class SemanticColumnItem(BaseModel):
    id: str
    table_id: str
    name: str
    type: str
    role: str
    description: str
    alias: list[str]
    examples: list
    nullable: bool
    sensitive: bool
    sync: bool
    enum_values: list


class SemanticColumnUpdate(BaseModel):
    description: str | None = None
    alias: list[str] | None = None
    examples: list | None = None
    role: str | None = Field(default=None, min_length=1, max_length=32)
    sensitive: bool | None = None
    sync: bool | None = None
    enum_values: list | None = None


class SemanticMetricItem(BaseModel):
    id: str
    name: str
    description: str
    alias: list[str]
    formula: str
    relevant_columns: list[str]
    filters: list[str]
    time_column: str | None
    unit: str
    dimensions: list[str]
    snapshot: bool


class SemanticMetricUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    alias: list[str] = []
    formula: str = ""
    relevant_columns: list[str] = []
    filters: list[str] = []
    time_column: str | None = None
    unit: str = Field(default="count", max_length=32)
    dimensions: list[str] = []
    snapshot: bool = False


class SemanticMetricCreate(SemanticMetricUpsert):
    id: str = Field(pattern="^[a-z][a-z0-9_]{0,63}$", description="指标编码，小写下划线")


class SemanticRelationshipItem(BaseModel):
    id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str
    condition: str | None
    physical: bool


class SemanticRelationshipUpsert(BaseModel):
    source_table: str = Field(min_length=1, max_length=64)
    source_column: str = Field(min_length=1, max_length=64)
    target_table: str = Field(min_length=1, max_length=64)
    target_column: str = Field(min_length=1, max_length=64)
    relationship_type: str = Field(default="many_to_one", max_length=32)
    condition: str | None = None
    physical: bool = True


class SemanticRelationshipCreate(SemanticRelationshipUpsert):
    id: str = Field(default="", max_length=160, description="留空则按源和目标自动生成")


class SemanticReleaseActivateRequest(BaseModel):
    build_id: str = Field(min_length=1, max_length=36)
    query_set_id: str | None = Field(default=None, min_length=1, max_length=36)
    domain: str = Field(default="audio", min_length=1, max_length=64)
    datasource: str | None = Field(default=None, min_length=1, max_length=128)


class SemanticReleaseItem(BaseModel):
    id: str
    version: int
    version_label: str
    domain: str
    datasource: str
    release_kind: Literal["activation", "rollback"]
    knowledge_build_id: str
    query_set_id: str
    query_set_version: int | None
    business_rule_set_id: str
    business_rule_set_version: int | None
    source_release_id: str | None
    created_by: str
    created_at: datetime
    active: bool
