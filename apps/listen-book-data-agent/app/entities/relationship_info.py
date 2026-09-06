from dataclasses import dataclass


@dataclass
class RelationshipInfo:
    id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str = "many_to_one"
    condition: str | None = None
    physical: bool = True
    build_id: str = "legacy"
