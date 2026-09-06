# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# =====================================================================
# Skill-Hub 抽象基类与上下文数据协议 (Skill Base & Context)
# 对应阿里 QwenPaw-Data (Skill-Hub) 核心设计：
# 将数仓业务分析方法论解耦为独立可插拔、可组合的执行技能（Skills），
# 而非将所有自然语言请求全压在单一脆弱的 Text2SQL 提示词上。
# =====================================================================

class SkillContext(BaseModel):
    """
    技能执行上下文，统一流转请求参数、会话状态与环境元数据
    """
    question: str = Field(..., description="用户自然语言问题")
    rewritten_question: str = Field("", description="多轮指代消解改写后的完整问题")
    dialect: str = Field("doris", description="目标数据库方言")
    user: str = Field("anonymous", description="用户名")
    role: str = Field("user", description="用户角色权限")
    recalled_meta: List[Dict[str, Any]] = Field(default_factory=list, description="召回的指标与维度元数据")
    user_preference: Dict[str, Any] = Field(default_factory=dict, description="用户偏好画像")
    extra_params: Dict[str, Any] = Field(default_factory=dict, description="额外自定义参数")


class SkillResult(BaseModel):
    """
    技能执行返回的标准结构化结果
    """
    success: bool
    skill_type: str = Field(..., description="技能类别: query / attribution / lineage / clarification")
    conclusion: Optional[str] = None
    chart: Optional[Dict[str, Any]] = None
    data: Optional[List[Dict[str, Any]]] = None
    column_types: Optional[Dict[str, str]] = None
    details: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    clarification: Optional[Dict[str, Any]] = None
    attribution_data: Optional[Dict[str, Any]] = None
    lineage_data: Optional[Dict[str, Any]] = None
    cache_hit: bool = False
    cache_type: Optional[str] = None


class BaseSkill(ABC):
    """
    技能抽象基类
    """
    name: str = "base_skill"
    description: str = "技能基类"

    @abstractmethod
    def can_handle(self, ctx: SkillContext) -> tuple[bool, float]:
        """
        判断当前技能对该问句的处理置信度 (0.0 ~ 1.0)
        """
        pass

    @abstractmethod
    def execute(self, ctx: SkillContext) -> SkillResult:
        """
        执行具体的数据分析或溯源动作
        """
        pass
