from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime

class TaskBase(BaseModel):
    name: str = Field(..., description="任务名称")
    agent_id: str = Field(..., description="绑定的智能体ID")
    cron_expr: str = Field(..., description="Cron 表达式")
    prompt: str = Field(..., description="执行指令")
    config: Optional[Dict[str, Any]] = None

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    name: Optional[str] = None
    agent_id: Optional[str] = None
    cron_expr: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[int] = None
    config: Optional[Dict[str, Any]] = None

class TaskResponse(TaskBase):
    id: int
    user_id: int
    creator_name: Optional[str] = None
    agent_name: Optional[str] = None
    conversation_id: str
    source: str = "web"
    status: int
    run_count: int = 0
    trigger_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    consecutive_failures: int = 0
    health_status: str = "unknown"
    last_status: Optional[str] = None
    last_message: Optional[str] = None
    last_error: Optional[str] = None
    last_attempt_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_alert_at: Optional[str] = None
    last_run_id: Optional[str] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TaskLogResponse(BaseModel):
    id: int
    trace_id: str
    query: str
    summary: Optional[str] = None
    status: str
    execution_time_ms: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
