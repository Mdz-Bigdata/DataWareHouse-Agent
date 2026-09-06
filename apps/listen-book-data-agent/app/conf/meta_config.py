from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ColumnConfig:
    name: str
    role: str | None = None
    description: str | None = None
    alias: list[str] = field(default_factory=list)
    sync: bool = False
    sensitive: bool = False


@dataclass
class TableConfig:
    name: str
    role: str
    description: str
    domain: str = "audio"
    alias: list[str] = field(default_factory=list)
    columns: list[ColumnConfig] = field(default_factory=list)


@dataclass
class PolymorphicRelationshipConfig:
    id: str
    source_table: str
    source_column: str
    discriminator_column: str
    targets: dict[str, str]
    target_column: str = "id"
    relationship_type: str = "many_to_one"


@dataclass
class MetricConfig:
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


@dataclass
class MetaConfig:
    domain: str = "audio"
    tables: Optional[list[TableConfig]] = None
    metrics: Optional[list[MetricConfig]] = None
    polymorphic_relationships: Optional[list[PolymorphicRelationshipConfig]] = None
    sensitive_columns: list[str] = field(default_factory=list)
