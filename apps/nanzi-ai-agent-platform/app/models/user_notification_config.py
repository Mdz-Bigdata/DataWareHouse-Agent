from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger
from datetime import datetime
from app.core.orm import Base

class UserNotificationConfig(Base):
    __tablename__ = "user_notification_configs"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    channel_type = Column(String(50), nullable=False) # dingtalk, wechat_work, email
    config_json = Column(Text, nullable=False) # JSON parameters as string
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
