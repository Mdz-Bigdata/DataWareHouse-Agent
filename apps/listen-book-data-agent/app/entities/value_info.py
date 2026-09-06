from dataclasses import dataclass

@dataclass
class ValueInfo:
    """存放ES字段取值文档"""

    id: str
    value: str
    column_id: str
    build_id: str = "legacy"
