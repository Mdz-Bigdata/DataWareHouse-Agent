from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, Index, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.orm import Base

class Role(Base):
    __tablename__ = "ai_agent_roles"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    users = relationship("User", secondary="ai_agent_user_role_relations", back_populates="roles")

class UserRoleRelation(Base):
    __tablename__ = "ai_agent_user_role_relations"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), index=True, nullable=False)
    role_id = Column(BigInteger, ForeignKey("ai_agent_roles.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

class ResourcePermission(Base):
    __tablename__ = "ai_agent_resource_permissions"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, index=True, nullable=True) # Intentionally no FK constraint in ORM for loose coupling, or add ForeignKey if strict
    role_id = Column(BigInteger, index=True, nullable=True)
    resource_type = Column(String(20), nullable=False) # agent, dataset, api
    resource_id = Column(String(100), nullable=False)
    enabled = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Composite indexes are defined in DB, but can be declared here in __table_args__ if needed for migrations
    # __table_args__ = (
    #     Index('idx_user_res', "user_id", "resource_type"),
    # )
