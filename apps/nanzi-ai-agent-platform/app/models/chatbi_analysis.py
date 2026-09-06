from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, JSON, String, Text

from app.core.orm import Base


class ChatBIBrief(Base):
    __tablename__ = "chatbi_briefs"

    id = Column(String(64), primary_key=True)
    owner_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    conversation_id = Column(String(128), nullable=False, index=True)
    result_id = Column(String(64), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    brief_payload = Column(JSON, nullable=False)
    markdown_content = Column(Text, nullable=False)
    artifact_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("idx_chatbi_briefs_owner_created", "owner_user_id", "created_at"),
    )
