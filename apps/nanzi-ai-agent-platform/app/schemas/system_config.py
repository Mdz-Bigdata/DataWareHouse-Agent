from pydantic import BaseModel
from typing import Optional

class ConfigHistoryItem(BaseModel):
    id: int
    config_key: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    description: Optional[str] = None
    changed_by: str
    change_type: str
    created_at: str
