from sqlalchemy import Column, BigInteger, String, Text, DateTime, Integer, Float, JSON, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.core.orm import Base

class ChatBIExample(Base):
    __tablename__ = "ai_chatbi_examples"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trace_id = Column(String(64), nullable=False, unique=True, index=True)
    agent_id = Column(String(36), nullable=False)
    dataset_id = Column(Integer, nullable=False)
    user_query = Column(Text, nullable=False)
    refined_query = Column(Text, nullable=True) # 新增：增强后的完整意图
    context_summary = Column(Text, nullable=True) # 新增：上下文背景
    sql_text = Column(Text, nullable=False)
    sql_metadata = Column(JSON, nullable=True) # 新增：SQL 特征元数据
    enhance_status = Column(String(20), nullable=False, default="pending") # 新增：增强状态
    ai_answer = Column(Text, nullable=True)
    feedback_type = Column(String(10), nullable=False, default="up")
    status = Column(String(20), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    use_count = Column(Integer, nullable=False, default=0)
    rag_doc_id = Column(String(64), nullable=True)
    rag_sync_status = Column(String(20), nullable=False, default="pending")
    rag_sync_error = Column(Text, nullable=True)
    rag_synced_at = Column(DateTime, nullable=True)
    user_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

class ChatBIExampleUsage(Base):
    __tablename__ = "ai_chatbi_example_usages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    example_id = Column(BigInteger, nullable=False, index=True)
    trace_id = Column(String(64), nullable=False, index=True)
    similarity = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
