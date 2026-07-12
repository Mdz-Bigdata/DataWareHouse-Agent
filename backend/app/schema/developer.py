# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

# NOTE: 定义数仓开发 Agent 协作接口的的 Pydantic 校验 Schema。

class DevRequest(BaseModel):
    requirement: str = Field(..., description="业务数仓建表与ETL开发需求说明")
    datasource: str = Field("doris", description="数仓数据源 (doris / starrocks / clickhouse)")
    sql_engine: str = Field("doris", description="SQL编写和执行引擎 (flinksql / sparksql / clickhouse / doris / starrocks / postgresql)")

class PhaseLog(BaseModel):
    agent: Optional[str] = None
    skill: Optional[str] = None
    action: str
    reviewer: Optional[str] = None
    review_status: Optional[str] = None
    review_comments: Optional[str] = None
    output: dict

class ChecklistItem(BaseModel):
    id: int
    step: str
    agent: str
    done: bool

class DevResponse(BaseModel):
    success: bool
    table_name: str
    db_name: str
    phases: List[PhaseLog]
    checklist: List[ChecklistItem]
