from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class LlmProviderMySQL(Base):
    """LLM 供应商配置。api_key_encrypted 为 Fernet 密文，任何接口不回显原文。"""

    __tablename__ = "llm_provider"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # deepseek / openai / openai_compatible（兼容服务可自定义 base_url）
    provider_type: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(256))
    model_name: Mapped[str] = mapped_column(String(128))
    api_key_encrypted: Mapped[str] = mapped_column(String(512))
    temperature: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
