from sqlalchemy import Column, String, Boolean, DateTime
from datetime import datetime
from app.core.orm import Base

class AIModel(Base):
    __tablename__ = "ai_models"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    model_id = Column(String(255), nullable=False)  # Actual Model ID for API
    provider = Column(String(50), nullable=False)   # e.g., openai, azure
    type = Column(String(50), nullable=False)       # e.g., llm, embedding
    
    api_base_url = Column(String(512), nullable=True)
    api_key = Column(String(512), nullable=True)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
