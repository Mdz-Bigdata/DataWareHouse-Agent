from sqlalchemy import Column, String, Text, DateTime, Integer, JSON, SmallInteger
from datetime import datetime
from app.core.orm import Base

class AgentScheduledTask(Base):
    """
    Model for cron-based agent automation tasks.
    """
    __tablename__ = "ai_agent_scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    user_id = Column(Integer, nullable=False)
    agent_id = Column(String(50), nullable=False)
    conversation_id = Column(String(50), nullable=False)
    cron_expr = Column(String(50), nullable=False)
    prompt = Column(Text, nullable=False)
    source = Column(String(20), default='web') # web, agent
    status = Column(SmallInteger, default=1)  # 0-Stopped, 1-Running, 2-Error
    config = Column(JSON, nullable=True)      # Webhook, retry, etc.
    run_count = Column(Integer, default=0, comment='累计执行次数')
    last_run_id = Column(String(50), nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
