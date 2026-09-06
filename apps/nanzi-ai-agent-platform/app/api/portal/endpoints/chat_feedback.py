from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.core.orm import get_db_session as get_db
from app.core.dependencies import require_api_key
from app.models.audit import AgentExecutionHistory
from app.services.chatbi_example_service import ExampleService

router = APIRouter()

class FeedbackRequest(BaseModel):
    trace_id: str
    feedback: str  # "up" or "down"
    user_id: Optional[str] = None

@router.post("/feedback")
async def collect_feedback(
    request: FeedbackRequest, 
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db), 
    _=Depends(require_api_key)
):
    """
    收集用户对 AI 回答的反馈。
    """
    if request.feedback not in ["up", "down"]:
        raise HTTPException(status_code=400, detail="Invalid feedback type. Must be 'up' or 'down'.")

    # 1. 更新执行历史表
    stmt = select(AgentExecutionHistory).filter(
        AgentExecutionHistory.trace_id == request.trace_id
    )
    result = await db.execute(stmt)
    history = result.scalars().first()
    
    if not history:
        raise HTTPException(status_code=404, detail="Trace not found.")

    try:
        # 使用 setattr 动态设置字段
        setattr(history, "feedback", request.feedback)
        await db.commit()
    except Exception as e:
        await db.rollback()
        pass

    # 2. 触发经验沉淀
    example = await ExampleService.create_from_feedback(
        db=db, 
        trace_id=request.trace_id, 
        feedback_type=request.feedback,
        user_id=request.user_id
    )

    return {
        "code": 200, 
        "message": "感谢您的反馈！", 
        "data": {"example_id": example.id if example else None}
    }
