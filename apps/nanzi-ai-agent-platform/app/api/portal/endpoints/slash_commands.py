from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.orm import get_db_session
from app.core.dependencies import get_current_user, require_admin
from app.services.slash_command_service import (
    SlashCommandService, 
    SlashCommandResponse, 
    SlashCommandCreate, 
    SlashCommandUpdate,
    SlashCommandReorderRequest
)

router = APIRouter()

@router.get("/", response_model=List[SlashCommandResponse])
async def list_commands(
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取所有快捷指令列表 (根据权限过滤)"""
    return await SlashCommandService.list_commands(session, user)

@router.post("/reorder")
async def reorder_commands(
    data: SlashCommandReorderRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """批量更新快捷指令排序"""
    success = await SlashCommandService.reorder_commands(session, data, user)
    return {"status": "success" if success else "failed"}

@router.post("/", response_model=SlashCommandResponse)
async def create_command(
    data: SlashCommandCreate,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """创建新的快捷指令"""
    return await SlashCommandService.create_command(session, data, user.get("user_name", "unknown"))

@router.put("/{cmd_id}", response_model=SlashCommandResponse)
async def update_command(
    cmd_id: int,
    data: SlashCommandUpdate,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """更新快捷指令"""
    cmd = await SlashCommandService.update_command(session, cmd_id, data, user)
    if not cmd:
        raise HTTPException(status_code=403, detail="Command not found or permission denied")
    return cmd

@router.delete("/{cmd_id}")
async def delete_command(
    cmd_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """删除快捷指令"""
    success = await SlashCommandService.delete_command(session, cmd_id, user)
    if not success:
        raise HTTPException(status_code=403, detail="Command not found or permission denied")
    return {"status": "success"}
