# -*- coding: utf-8 -*-
from fastapi import APIRouter, HTTPException, Query
from typing import List
from app.schema.chat import AskRequest, AskResponse, HistoryRecord, PreferenceProfile
from app.service.ask_agent import ask_agent
from app.model.user_memory import user_memory

# NOTE: API 控制器层 - 智能问数接口路由，处理自然语言问数、历史记录及偏好查询。

router = APIRouter(prefix="/chat", tags=["智能问数"])

@router.post("/ask", response_model=AskResponse)
def ask_question(request: AskRequest):
    """
    接收自然语言问题，执行 NL2SQL 全链路问数 Agent 动作
    """
    try:
        res = ask_agent.ask(
            question=request.question,
            dialect=request.dialect,
            user=request.user,
            role=request.role
        )
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"智能问数内部错误: {str(e)}")

@router.get("/history", response_model=List[HistoryRecord])
def get_chat_history(user: str = Query("anonymous", description="用户名")):
    """
    获取 L1 查询历史记录
    """
    return user_memory.get_history(user=user)

@router.get("/preference", response_model=PreferenceProfile)
def get_user_preference(user: str = Query("anonymous", description="用户名")):
    """
    获取 L2 用户画像偏好
    """
    profile = user_memory.get_preference_profile(user=user)
    return profile

@router.post("/preference", response_model=PreferenceProfile)
def update_user_preference(request: PreferenceProfile):
    """
    更新/覆盖 L2 用户画像偏好
    """
    try:
        updated_profile = user_memory.update_preference_profile(
            user=request.user,
            profile_update={
                "common_tables": request.common_tables,
                "common_metrics": request.common_metrics,
                "common_dimensions": request.common_dimensions,
                "common_time_ranges": request.common_time_ranges
            }
        )
        return updated_profile
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存画像失败: {str(e)}")

@router.get("/recommendations", response_model=List[str])
def get_active_recommendations(user: str = Query("anonymous", description="用户名")):
    """
    获取 L3 主动建议推荐列表
    """
    return user_memory.get_active_recommendations(user=user)
