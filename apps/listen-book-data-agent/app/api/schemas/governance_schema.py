from typing import Literal

from pydantic import BaseModel, Field


class TermBindingSchema(BaseModel):
    kind: Literal["column", "metric", "table", "value"]
    semantic_id: str = Field(min_length=1, max_length=160)


class SemanticTermCreate(BaseModel):
    term_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    standard_term: str = Field(min_length=1, max_length=128)
    synonyms: list[str] = Field(default_factory=list)
    description: str = ""
    bindings: list[TermBindingSchema] = Field(default_factory=list)
    domain: str = Field(default="audio", min_length=1, max_length=64)
    datasource: str | None = Field(default=None, min_length=1, max_length=128)


class SemanticTermItem(BaseModel):
    id: str
    term_key: str
    standard_term: str
    synonyms: list[str]
    description: str
    bindings: list[dict]
    domain: str
    datasource: str
    status: str
    version: int
    created_by: str | None


class ParameterSchema(BaseModel):
    name: str = Field(pattern=r"^p[1-9][0-9]*$")
    type: Literal["boolean", "date", "datetime", "integer", "number", "string"]
    required: bool = True


class VerifiedQueryCreate(BaseModel):
    case_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    question: str = Field(min_length=1, max_length=500)
    dialect: str = Field(default="mysql", max_length=32)
    sql_template: str = Field(min_length=1)
    parameter_schema: list[ParameterSchema] = Field(default_factory=list)
    expected_fields: list[str] = Field(min_length=1)
    expected_metrics: list[str] = Field(default_factory=list)
    assertions: list[dict] = Field(default_factory=list)
    domain: str = Field(default="audio", min_length=1, max_length=64)
    datasource: str | None = Field(default=None, min_length=1, max_length=128)
    source_trace_id: str | None = None
    source: Literal["feedback", "manual", "trace"] = "manual"


class VerifiedQueryReview(BaseModel):
    approved: bool


class VerifiedQueryItem(BaseModel):
    id: str
    case_key: str
    revision: int
    domain: str
    datasource: str
    question: str
    dialect: str
    sql_template: str
    parameter_schema: list[dict]
    expected_fields: list[str]
    expected_metrics: list[str]
    assertions: list[dict]
    source_trace_id: str | None
    source: str
    lifecycle: str
    created_by: str | None
    reviewer_id: str | None


class QuerySetPublishRequest(BaseModel):
    domain: str = Field(default="audio", min_length=1, max_length=64)
    datasource: str | None = Field(default=None, min_length=1, max_length=128)


class QuerySetItem(BaseModel):
    id: str
    version: int
    version_label: str
    domain: str
    datasource: str
    content_hash: str
    manifest: list[dict]
    status: str
    created_by: str
    reviewer_id: str


class BusinessRuleCreate(BaseModel):
    rule_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    rule_type: Literal[
        "display_convention",
        "filter_requirement",
        "join_requirement",
        "metric_constraint",
        "time_interpretation",
    ]
    content: str = Field(min_length=1, max_length=1000)
    domain: str = Field(default="audio", min_length=1, max_length=64)
    datasource: str | None = Field(default=None, min_length=1, max_length=128)
    intents: list[Literal["aggregate", "compare", "detail", "ranking", "trend"]] = Field(
        default_factory=list
    )
    semantic_ids: list[str] = Field(default_factory=list, max_length=50)
    priority: int = Field(default=100, ge=0, le=1000)


class BusinessRuleReview(BaseModel):
    approved: bool


class BusinessRuleItem(BaseModel):
    id: str
    rule_key: str
    version: int
    rule_type: str
    content: str
    domain: str
    datasource: str
    intents: list[str]
    semantic_ids: list[str]
    priority: int
    status: str
    created_by: str | None
    reviewer_id: str | None
