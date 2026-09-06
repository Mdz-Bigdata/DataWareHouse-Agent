from typing import List, Optional
from pydantic import BaseModel

class TableListResponse(BaseModel):
    tables: List[str]

class ColumnDefinition(BaseModel):
    name: str
    type: str
    comment: Optional[str] = None

class ColumnListResponse(BaseModel):
    columns: List[ColumnDefinition]

class ColumnIntrospectRequest(BaseModel):
    data_source: str = "clickhouse"
    table_name: Optional[str] = None
    custom_sql: Optional[str] = None
