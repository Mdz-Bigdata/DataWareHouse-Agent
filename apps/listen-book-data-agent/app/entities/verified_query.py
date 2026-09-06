from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerifiedQueryRevision:
    id: str
    case_key: str
    revision: int
    domain: str
    datasource: str
    question: str
    dialect: str
    sql_template: str
    parameter_schema: list[dict] = field(default_factory=list)
    expected_fields: list[str] = field(default_factory=list)
    expected_metrics: list[str] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    source_trace_id: str | None = None
    source: str = "manual"
    lifecycle: str = "candidate"
    created_by: str | None = None
    reviewer_id: str | None = None


@dataclass(frozen=True)
class QuerySetVersion:
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


@dataclass(frozen=True)
class VerifiedQueryExample:
    """A published Query Set case safe to use as a scoped retrieval example."""

    query_set_id: str
    query_set_version: int
    query_set_hash: str
    domain: str
    datasource: str
    revision_id: str
    case_key: str
    question: str
    dialect: str
    sql_template: str
    parameter_schema: list[dict] = field(default_factory=list)
    expected_fields: list[str] = field(default_factory=list)
    expected_metrics: list[str] = field(default_factory=list)
    score: float = 0.0
