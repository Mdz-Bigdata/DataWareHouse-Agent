from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime

from app.core.orm import get_db_session
from app.services.changelog_service import ChangelogService
from app.schemas.changelog import (
    ChangelogResponse, 
    ChangelogQueryParams, 
    ChangelogStatsResponse,
    ChangeDiffResponse
)
from app.core.dependencies import get_current_user, require_permission
from app.models.changelog import MetaChangelog

router = APIRouter()

@router.get("/datasets/{dataset_id}", response_model=List[ChangelogResponse])
async def get_dataset_changelog(
    dataset_id: int,
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取数据集的变更历史"""
    return await ChangelogService.get_dataset_changelog(
        conn, dataset_id, limit=limit, offset=offset
    )

@router.get("", response_model=List[ChangelogResponse])
async def get_changelog(
    resource_type: Optional[str] = Query(None, description="资源类型"),
    operation: Optional[str] = Query(None, description="操作类型"),
    user_id: Optional[int] = Query(None, description="用户ID"),
    user_name: Optional[str] = Query(None, description="用户名"),
    start_date: Optional[datetime] = Query(None, description="开始时间"),
    end_date: Optional[datetime] = Query(None, description="结束时间"),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """根据条件查询变更日志"""
    return await ChangelogService.get_changelog_with_filters(
        conn,
        resource_type=resource_type,
        operation=operation,
        user_id=user_id,
        user_name=user_name,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset
    )

@router.get("/stats", response_model=List[ChangelogStatsResponse])
async def get_changelog_stats(
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取变更统计信息"""
    return await ChangelogService.get_changelog_stats(conn, days=days)

@router.get("/{changelog_id}/diff", response_model=ChangeDiffResponse)
async def get_change_diff(
    changelog_id: int,
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取变更详情对比"""
    # 获取变更记录
    stmt = select(MetaChangelog).where(MetaChangelog.id == changelog_id)
    result = await conn.execute(stmt)
    changelog_record = result.scalar_one_or_none()
    
    if not changelog_record:
        raise HTTPException(status_code=404, detail="变更记录不存在")
    
    # 生成对比数据
    changes = []
    if changelog_record.operation == "update":
        old_data = changelog_record.old_data or {}
        new_data = changelog_record.new_data or {}
        
        for field in changelog_record.changed_fields or []:
            changes.append({
                "field": field,
                "old_value": old_data.get(field),
                "new_value": new_data.get(field)
            })
    elif changelog_record.operation == "create":
        changes.append({
            "field": "全部数据",
            "old_value": None,
            "new_value": changelog_record.new_data
        })
    elif changelog_record.operation == "delete":
        changes.append({
            "field": "全部数据",
            "old_value": changelog_record.old_data,
            "new_value": None
        })
    
    # 生成摘要
    operation_map = {
        "create": "创建",
        "update": "更新", 
        "delete": "删除"
    }
    resource_type_map = {
        "dataset": "数据集",
        "table": "表",
        "column": "字段",
        "metric": "指标",
        "relationship": "关系"
    }
    
    summary = f"{operation_map.get(changelog_record.operation, changelog_record.operation)}" \
              f"{resource_type_map.get(changelog_record.resource_type, changelog_record.resource_type)}" \
              f" - {changelog_record.resource_id}"
    
    return ChangeDiffResponse(
        operation=changelog_record.operation,
        resource_name=changelog_record.resource_id,
        changes=changes,
        summary=summary
    )

@router.get("/{resource_type}/{resource_id}", response_model=List[ChangelogResponse])
async def get_resource_changelog(
    resource_type: str,
    resource_id: str,
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    conn: AsyncSession = Depends(get_db_session),
    user: dict = Depends(require_permission("menu", "menu:metadata"))
):
    """获取指定资源的变更历史"""
    return await ChangelogService.get_changelog_by_resource(
        conn, resource_type, resource_id, limit=limit, offset=offset
    )
