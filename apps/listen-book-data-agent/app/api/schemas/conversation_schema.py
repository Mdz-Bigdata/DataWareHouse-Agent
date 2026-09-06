from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConversationCreate(BaseModel):
    title: str = Field(default="新分析", min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("会话标题不能为空")
        return normalized


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "archived"] | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("会话标题不能为空")
        return normalized

    @model_validator(mode="after")
    def require_change(self):
        if self.title is None and self.status is None:
            raise ValueError("至少提供一个修改字段")
        return self


class ConversationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class ConversationTurnItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query_text: str
    standalone_question: str | None
    status: str
    total_duration_ms: int | None
    started_at: datetime
    completed_at: datetime | None
    parent_trace_id: str | None
    regenerate_of_trace_id: str | None
    query_plan_summary: dict | None
    answer_summary: str | None
    chart_spec: dict | None
    sql: str | None
    build_id: str | None
    policy_version: str | None
    policy_hash: str | None
    semantic_release_id: str | None
    semantic_release_version: int | None
    query_set_id: str | None
    query_set_version: int | None
    business_rule_set_id: str | None
    business_rule_set_version: int | None
