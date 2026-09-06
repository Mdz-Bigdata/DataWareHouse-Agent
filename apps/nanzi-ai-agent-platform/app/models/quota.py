from sqlalchemy import Column, String, Boolean, DateTime, BigInteger
from datetime import datetime
from app.core.orm import Base


class QuotaPolicy(Base):
    __tablename__ = "ai_agent_quota_policies"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    scope_type = Column(String(20), nullable=False, comment="user|role|system")
    scope_id = Column(BigInteger, nullable=True, comment="user_id 或 role_id")
    period = Column(String(20), nullable=False, default="monthly")
    limit_tokens = Column(BigInteger, nullable=True, comment="NULL 表示不限额")
    enabled = Column(Boolean, default=True)
    action_on_exceed = Column(String(20), nullable=False, default="block")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
