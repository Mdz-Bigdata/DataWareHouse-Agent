from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from enum import Enum

class PromptSource(str, Enum):
    SYSTEM_CONFIG = "system_config"
    AGENT = "agent"

class PromptVersionSummary(BaseModel):
    version_number: int
    status: str
    comment: Optional[str] = None
    updated_at: str

class PromptMetadata(BaseModel):
    id: str  # config_key or agent_id
    name: str
    display_name: Optional[str] = None
    source: PromptSource
    category: str
    description: Optional[str] = None
    
    # Association info
    target_key: Optional[str] = None  # for system_config
    agent_id: Optional[str] = None     # for agent
    created_by: Optional[str] = None
    is_system: bool = False
    
    current_version: Optional[int] = None
    versions: List[PromptVersionSummary] = []

class PromptDetail(BaseModel):
    id: str
    source: PromptSource
    content: str
    version_number: Optional[int] = None
    version_note: Optional[str] = None # Added field for displaying history comment
    variables: List[str] = []

class PromptTestRequest(BaseModel):
    content: str
    variables: Dict[str, Any] = {}
    user_input: Optional[str] = None
    model: Optional[str] = None

class PromptTestResponse(BaseModel):
    raw_output: str
    interpolated_prompt: str
    latency_ms: float

class PromptSaveRequest(BaseModel):
    source: PromptSource
    target_id: str
    content: str
    version_note: str = ""

class PromptOptimizeItem(BaseModel):
    title: str = Field(..., description="优化策略标题")
    content: str = Field(..., description="优化后的提示词内容")
    reason: str = Field(..., description="推荐理由")

class PromptOptimizeResponse(BaseModel):
    suggestions: List[PromptOptimizeItem]
