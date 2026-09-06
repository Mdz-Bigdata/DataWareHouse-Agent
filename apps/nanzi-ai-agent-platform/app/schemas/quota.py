from typing import Optional, Literal
from pydantic import BaseModel, Field


class QuotaPolicyUpdate(BaseModel):
    enabled: bool = True
    limit_tokens: Optional[int] = Field(
        None,
        ge=0,
        description="月 Token 上限；null 表示不限额",
    )


class QuotaStatusResponse(BaseModel):
    period: str = "monthly"
    period_start: str
    period_end: str
    used_tokens: int = 0
    limit_tokens: Optional[int] = None
    remaining_tokens: Optional[int] = None
    source: Literal["user", "role", "system", "unlimited", "admin_bypass"] = "unlimited"
    source_label: Optional[str] = None
    action_on_exceed: str = "block"
    is_admin_bypass: bool = False
    policy_enabled: bool = True


class QuotaPolicyResponse(BaseModel):
    scope_type: str
    scope_id: Optional[int] = None
    enabled: bool = False
    limit_tokens: Optional[int] = None
    action_on_exceed: str = "block"
    inherit: bool = True
    effective: Optional[QuotaStatusResponse] = None
