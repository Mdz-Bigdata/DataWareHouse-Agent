from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime


class DbConnectionConfigCreate(BaseModel):
    """创建连接配置请求体"""
    name: str
    db_type: str
    host: str
    port: int
    db_user: str
    password: str
    database_name: str
    description: str = ""


class DbConnectionConfigResponse(DbConnectionConfigCreate):
    """连接配置响应体（密码不回传）"""
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DbConnectionConfigSafeResponse(BaseModel):
    """前端用响应体，密码脱敏"""
    id: int
    name: str
    db_type: str
    host: str
    port: int
    db_user: str
    password: str  # 回传明文，供前端填充表单（历史连接一键填充）
    database_name: str
    description: str = ""
    created_by: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DbProfileTaskResponse(BaseModel):
    id: int
    connection_id: int
    status: int
    total_tables: int
    processed_tables: int
    current_table: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DbTableProfileResponse(BaseModel):
    id: int
    connection_id: int
    table_name: str
    table_type: str
    engine: Optional[str] = None
    ddl: Optional[str] = None
    sample_data: Optional[str] = None
    ai_term: Optional[str] = None
    ai_description: Optional[str] = None
    ai_tags: Optional[list[str]] = None
    columns_profile: Optional[list[dict]] = None
    status: int
    confidence_score: int
    is_temporary: int
    is_ignored: int
    confidence_reason: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DbTableProfileSummaryResponse(BaseModel):
    """列表摘要：不含 ddl / sample_data / columns_profile，适合大库分页浏览。"""
    id: int
    connection_id: int
    table_name: str
    table_type: str
    engine: Optional[str] = None
    ai_term: Optional[str] = None
    ai_description: Optional[str] = None
    ai_tags: Optional[list[str]] = None
    columns_count: int = 0
    status: int
    confidence_score: int
    is_temporary: int
    is_ignored: int
    confidence_reason: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DbTableProfileTagStat(BaseModel):
    name: str
    count: int


class DbTableProfileStatsResponse(BaseModel):
    total: int = 0
    table_count: int = 0
    view_count: int = 0
    field_count: int = 0
    success_count: int = 0
    importable_success_count: int = 0
    ignored_count: int = 0
    last_profiled_at: Optional[datetime] = None
    tags: list[DbTableProfileTagStat] = Field(default_factory=list)


class DbTableProfilePageResponse(BaseModel):
    items: list[DbTableProfileSummaryResponse]
    total: int
    page: int
    page_size: int
    pages: int


class ProfileImportPreviewRequest(BaseModel):
    table_names: list[str] = Field(min_length=1)

