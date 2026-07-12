# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from typing import List, Optional, Any

# NOTE: 定义智能问数接口的 Pydantic 校验 Schema。

class AskRequest(BaseModel):
    question: str = Field(..., description="用户的自然语言问题")
    dialect: str = Field("doris", description="目标数据库方言 (clickhouse / doris / postgres)")
    user: str = Field("anonymous", description="当前提问的用户名")
    role: Optional[str] = Field("user", description="用户角色权限 (user / analyst / admin)")

class ChartConfig(BaseModel):
    type: str
    title: str
    config: dict

class QueryDetails(BaseModel):
    sql: str
    dialect: str
    elapsed_time: str
    tables: List[str]
    source_desc: str
    filters: List[dict]
    estimated_rows: Optional[int] = 0

class ClarificationOption(BaseModel):
    label: str
    query: str

class ClarificationInfo(BaseModel):
    need_clarification: bool = False
    message: str = ""
    options: List[ClarificationOption] = []

class AskResponse(BaseModel):
    success: bool
    conclusion: Optional[str] = None
    chart: Optional[ChartConfig] = None
    data: Optional[List[dict]] = None
    column_types: Optional[dict] = None
    error: Optional[str] = None
    details: Optional[QueryDetails] = None
    clarification: Optional[ClarificationInfo] = None

class HistoryRecord(BaseModel):
    id: int
    user: str
    question: str
    sql: str
    dialect: str
    execution_time: str
    result_summary: str
    created_at: str

class PreferenceProfile(BaseModel):
    user: str
    common_tables: List[dict]
    common_metrics: List[dict]
    common_dimensions: List[dict]
    common_time_ranges: List[dict]


class QueryDSL(BaseModel):
    metrics: List[dict] = Field(default_factory=list, description="涉及指标列表，例如：[{'name': 'gmv', 'agg': 'SUM'}]")
    dimensions: List[dict] = Field(default_factory=list, description="剖析维度列表，例如：[{'name': 'region_name'}]")
    filters: List[dict] = Field(default_factory=list, description="过滤条件列表，例如：[{'field': 'region_name', 'op': 'eq', 'value': '华东'}]")
    time_range: Optional[dict] = Field(default=None, description="时间范围，例如：{'start': '2026-05-01', 'end': '2026-05-31', 'grain': 'day'}")
    order_by: Optional[List[dict]] = Field(default=None, description="排序规则，例如：[{'field': 'gmv', 'direction': 'desc'}]")
    limit: Optional[int] = Field(default=10, description="返回行数限制，默认 10")

