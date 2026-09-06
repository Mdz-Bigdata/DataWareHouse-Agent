from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.orm import Base

class McpServer(Base):
    __tablename__ = "sys_mcp_servers"

    id = Column(String(36), primary_key=True)
    server_name = Column(String(100), nullable=False, unique=True)
    sse_url = Column(Text, nullable=False)
    auth_headers = Column(Text, nullable=True)  # JSON string
    enabled_status = Column(Integer, default=0) # 0: Offline/Disabled, 1: Online/Enabled
    last_sync_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    tools = relationship("McpToolCache", back_populates="server", cascade="all, delete-orphan")

class McpToolCache(Base):
    __tablename__ = "sys_mcp_tool_cache"

    id = Column(String(36), primary_key=True)
    server_id = Column(String(36), ForeignKey("sys_mcp_servers.id"), nullable=False)
    tool_name = Column(String(255), nullable=False)
    tool_description = Column(Text, nullable=True)
    parameter_schema = Column(Text, nullable=True) # JSON Schema string
    is_published = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    server = relationship("McpServer", back_populates="tools")
