from datetime import datetime

from pydantic import BaseModel, Field

PROVIDER_TYPES = ("deepseek", "openai", "openai_compatible")


class LlmProviderItem(BaseModel):
    """列表/详情返回：api_key 只回脱敏串，绝不出原文。"""

    id: str
    name: str
    provider_type: str
    base_url: str
    model_name: str
    api_key_masked: str
    temperature: float
    timeout_seconds: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LlmProviderUpsert(BaseModel):
    """新增/编辑。编辑时 api_key 传空表示保持原密钥不变。"""

    name: str = Field(min_length=1, max_length=64)
    provider_type: str = Field(pattern="^(deepseek|openai|openai_compatible)$")
    base_url: str = Field(min_length=1, max_length=256)
    model_name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(default="", max_length=256)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=60, ge=5, le=600)


class LlmProviderTestRequest(BaseModel):
    """连接测试：可传草稿配置（未保存的表单）；api_key 为空时用已存密钥。"""

    provider_type: str = Field(pattern="^(deepseek|openai|openai_compatible)$")
    base_url: str = Field(min_length=1, max_length=256)
    model_name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(default="", max_length=256)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class LlmProviderTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None
