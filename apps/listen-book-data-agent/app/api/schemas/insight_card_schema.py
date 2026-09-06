from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InsightCardExecuteRequest(BaseModel):
    conversation_id: str | None = None
    parent_trace_id: str | None = None


class InsightCardItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question: str
    answer_summary: str
    sql_template: str
    parameter_types: list[str]
    chart_spec: dict
    version_info: dict
    created_at: datetime
