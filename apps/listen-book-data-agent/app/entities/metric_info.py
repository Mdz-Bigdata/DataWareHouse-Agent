from dataclasses import dataclass, field

@dataclass
class MetricInfo:
    id: str
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]
    formula: str = ""
    filters: list[str] = field(default_factory=list)
    time_column: str | None = None
    unit: str = "count"
    currency_column: str | None = None
    dimensions: list[str] = field(default_factory=list)
    snapshot: bool = False
    build_id: str = "legacy"
