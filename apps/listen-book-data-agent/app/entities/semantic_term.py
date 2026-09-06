from dataclasses import dataclass, field


@dataclass(frozen=True)
class SemanticTerm:
    id: str
    term_key: str
    standard_term: str
    synonyms: list[str] = field(default_factory=list)
    description: str = ""
    bindings: list[dict] = field(default_factory=list)
    domain: str = "audio"
    datasource: str = ""
    status: str = "draft"
    version: int = 1
    created_by: str | None = None
