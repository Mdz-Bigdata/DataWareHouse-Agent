from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.query_schema import QueryParameterValue


class TraceItem(BaseModel):
    """查询记录条目：只含元数据，结果行从不落库也无从返回。"""

    id: str
    query_text: str
    status: str
    total_duration_ms: int | None
    started_at: datetime
    completed_at: datetime | None
    conversation_id: str | None = None
    parent_trace_id: str | None = None
    regenerate_of_trace_id: str | None = None
    standalone_question: str | None = None


type FeedbackReason = Literal[
    "accurate",
    "clear",
    "helpful",
    "wrong_metric",
    "wrong_filter",
    "wrong_join",
    "wrong_time_range",
    "wrong_granularity",
    "missing_data",
    "other",
]


class TraceFeedbackCreate(BaseModel):
    verdict: Literal["correct", "incorrect"]
    reasons: list[FeedbackReason] = Field(min_length=1, max_length=5)
    comment: str = Field(default="", max_length=1000)


class TraceFeedbackItem(BaseModel):
    id: str
    trace_id: str
    verdict: str
    reasons: list[str]
    comment: str
    template_signature: str
    candidate_revision_id: str | None
    positive_count: int | None


class TraceRegenerateRequest(BaseModel):
    parameters: dict[str, QueryParameterValue] = Field(default_factory=dict)


class DeepAnalysisFact(BaseModel):
    fact_id: str
    statement: str
    evidence_ids: list[str]


class DeepAnalysisInference(BaseModel):
    inference_id: str
    statement: str
    fact_ids: list[str]
    confidence: Literal["low", "medium", "high"]


class DeepAnalysisEvidence(BaseModel):
    evidence_id: str
    description: str
    values: dict[str, str | int | float | bool | None]


class DeepAnalysisItem(BaseModel):
    trace_id: str
    source_trace_id: str
    status: Literal["completed"]
    facts: list[DeepAnalysisFact]
    inferences: list[DeepAnalysisInference]
    evidence: list[DeepAnalysisEvidence]
    rerun_row_count: int
    row_limit: int
    truncated: bool
    policy_version: str
    policy_hash: str
    build_id: str
    disclaimer: str
