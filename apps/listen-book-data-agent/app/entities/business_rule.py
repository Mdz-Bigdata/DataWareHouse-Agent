from dataclasses import dataclass, field


@dataclass(frozen=True)
class BusinessRuleRevision:
    id: str
    rule_key: str
    version: int
    rule_type: str
    content: str
    domain: str
    datasource: str
    intents: list[str] = field(default_factory=list)
    semantic_ids: list[str] = field(default_factory=list)
    priority: int = 100
    status: str = "draft"
    created_by: str | None = None
    reviewer_id: str | None = None
