from dataclasses import dataclass, field
from typing import Any

@dataclass
class ColumnInfo:
    id: str
    name: str
    type: str
    role: str
    examples: list[Any]
    description: str
    alias: list[str]
    table_id: str
    nullable: bool = True
    sensitive: bool = False
    sync: bool = False
    enum_values: list[str] = field(default_factory=list)
    build_id: str = "legacy"
