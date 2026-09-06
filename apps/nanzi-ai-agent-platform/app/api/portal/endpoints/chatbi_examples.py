from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, select, func
from typing import Optional, List

from app.core.orm import get_db_session as get_db
from app.core.dependencies import require_api_key
from app.models.chatbi_example import ChatBIExample
from app.models.user import User
from app.models.agent import AIAgent
from app.services.chatbi_example_service import ExampleService

router = APIRouter()

class AuditRequest(BaseModel):
    id: int
    status: str # "approved", "rejected", "deprecated"

class UpdateExampleRequest(BaseModel):
    user_query: Optional[str] = None
    refined_query: Optional[str] = None
    context_summary: Optional[str] = None
    sql_text: Optional[str] = None
    sql_metadata: Optional[dict] = None

@router.post("/{id}/enhance")
async def trigger_manual_enhance(
    id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    手动触发 AI 智能增强（意图还原、背景总结）。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.id == id)
    result = await db.execute(stmt)
    example = result.scalars().first()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    # 设为 pending 并触发后台任务
    example.enhance_status = "pending"
    await db.commit()

    background_tasks.add_task(ExampleService._enhance_example_with_llm, id)
    return {"code": 200, "message": "智能增强任务已启动。"}

@router.put("/{id}")
async def update_example(
    id: int,
    request: UpdateExampleRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    更新 ChatBI 经验内容（支持手动微调提问、意图和上下文）。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.id == id)
    result = await db.execute(stmt)
    example = result.scalars().first()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    if request.user_query is not None:
        example.user_query = request.user_query
    if request.refined_query is not None:
        example.refined_query = request.refined_query
    if request.context_summary is not None:
        example.context_summary = request.context_summary
    if request.sql_text is not None:
        example.sql_text = request.sql_text
    if request.sql_metadata is not None:
        example.sql_metadata = request.sql_metadata
    # 修改内容后，重置同步状态，提醒需要重新同步
    if example.rag_sync_status == "synced":
        example.rag_sync_status = "pending"

    await db.commit()
    return {"code": 200, "message": "更新成功。"}

@router.get("")
async def list_examples(
    id: Optional[int] = None,
    agent_id: Optional[str] = None,
    dataset_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    获取 ChatBI 经验库列表（含用户与智能体信息）。
    """
    # 采用外连接获取用户信息和智能体信息
    stmt = select(
        ChatBIExample, 
        User.real_name.label("user_real_name"),
        User.user_name.label("user_account_name"),
        AIAgent.display_name.label("agent_display_name")
    ).outerjoin(
        User, User.id == ChatBIExample.user_id
    ).outerjoin(
        AIAgent, AIAgent.id == ChatBIExample.agent_id
    )

    if id:
        stmt = stmt.filter(ChatBIExample.id == id)
    if agent_id:
        stmt = stmt.filter(ChatBIExample.agent_id == agent_id)

    if dataset_id:
        stmt = stmt.filter(ChatBIExample.dataset_id == dataset_id)
    if status:
        stmt = stmt.filter(ChatBIExample.status == status)

    # 获取总数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    # 获取数据
    stmt = stmt.order_by(desc(ChatBIExample.created_at)).offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    
    # 转换为字典列表，合并显示名称
    items = []
    for row in result.all():
        example = row[0]
        real_name = row[1]
        account_name = row[2]
        agent_display_name = row[3]
        
        # 将 SQLAlchemy 对象转为字典
        item_dict = {c.name: getattr(example, c.name) for c in example.__table__.columns}
        
        # 优先级：真实姓名 > 账号名 > ID > 系统
        display_name = real_name or account_name
        if not display_name:
            display_name = f"ID:{example.user_id}" if example.user_id else "系统"
            
        item_dict["user_real_name"] = display_name
        item_dict["agent_display_name"] = agent_display_name or f"ID:{example.agent_id}"
        items.append(item_dict)

    return {
        "code": 200,
        "data": {
            "total": total,
            "items": items,
            "page": page,
            "size": size
        }
    }

@router.post("/sync-all")
async def sync_all_examples(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    一键同步所有审核通过 (approved) 的案例至 RAGFlow。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.status == "approved")
    result = await db.execute(stmt)
    examples = result.scalars().all()
    
    if not examples:
        return {"code": 200, "message": "当前没有状态为'已通过'的案例需要同步。"}

    for ex in examples:
        background_tasks.add_task(ExampleService.sync_to_ragflow, ex.id)
        
    return {"code": 200, "message": f"已成功触发 {len(examples)} 条已通过案例的同步任务。"}

@router.post("/audit")
async def audit_example(
    request: AuditRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    审核 ChatBI 经验。
    """
    if request.status not in ["approved", "rejected", "deprecated"]:
        raise HTTPException(status_code=400, detail="Invalid status.")

    success = await ExampleService.audit_example(db, request.id, request.status)
    if not success:
        raise HTTPException(status_code=404, detail="Example not found.")

    return {"code": 200, "message": "审核操作成功。"}

@router.post("/sync/{example_id}")
async def sync_example(
    example_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_api_key)
):
    """
    手动触发同步至 RAGFlow。
    """
    stmt = select(ChatBIExample).where(ChatBIExample.id == example_id)
    result = await db.execute(stmt)
    example = result.scalars().first()

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    if example.status not in ["approved", "deprecated"]:
        raise HTTPException(
            status_code=400, 
            detail=f"只有审核通过或已废弃的记录允许同步。当前状态: {example.status}"
        )

    background_tasks.add_task(ExampleService.sync_to_ragflow, example_id)
    return {"code": 200, "message": "已开启异步同步任务。"}
