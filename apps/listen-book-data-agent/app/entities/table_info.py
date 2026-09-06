from dataclasses import dataclass, field


@dataclass
class TableInfo:
    id: str
    name: str
    role: str
    description: str
    domain: str = "audio"
    alias: list[str] = field(default_factory=list)
    build_id: str = "legacy"
