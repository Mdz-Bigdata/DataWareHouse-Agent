"""Phase 3.4：数据源管理 API（admin 专属）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import get_meta_session
from app.api.deps import require_admin
from app.api.schemas.datasource_schema import (
    DatasourceItem,
    DatasourceUpdate,
    DatasourceUpsert,
)
from app.entities.datasource_info import DatasourceInfo
from app.models.mysql.user_mysql import UserMySQL
from app.services.datasource_service import DatasourceService, mask_datasource_password

admin_datasource_router = APIRouter(tags=["数据源管理"])


def _to_item(datasource: DatasourceInfo) -> DatasourceItem:
    """实体转展示项，密码脱敏。"""

    return DatasourceItem(
        id=datasource.id,
        name=datasource.name,
        dialect=datasource.dialect,
        host=datasource.host,
        port=datasource.port,
        database=datasource.database,
        user=datasource.user,
        password_masked=mask_datasource_password(datasource),
        active=datasource.active,
        description=datasource.description,
    )


@admin_datasource_router.get("/api/admin/datasources", response_model=list[DatasourceItem])
async def list_datasources(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = DatasourceService(meta_session)
    datasources = await service.list_datasources()
    return [_to_item(item) for item in datasources]


@admin_datasource_router.post(
    "/api/admin/datasources",
    response_model=DatasourceItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_datasource(
    body: DatasourceUpsert,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = DatasourceService(meta_session)
    existing = await service.get_datasource(body.id)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="数据源标识已存在")
    datasource = DatasourceInfo(
        id=body.id,
        name=body.name,
        dialect=body.dialect,
        host=body.host,
        port=body.port,
        database=body.database,
        user=body.user,
        password=body.password,
        active=body.active,
        description=body.description,
    )
    created = await service.create_datasource(datasource)
    return _to_item(created)


@admin_datasource_router.put(
    "/api/admin/datasources/{datasource_id}", response_model=DatasourceItem
)
async def update_datasource(
    datasource_id: str,
    body: DatasourceUpdate,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = DatasourceService(meta_session)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未提供任何更新字段")
    updated = await service.update_datasource(datasource_id, **fields)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    return _to_item(updated)


@admin_datasource_router.delete(
    "/api/admin/datasources/{datasource_id}", status_code=status.HTTP_200_OK
)
async def delete_datasource(
    datasource_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = DatasourceService(meta_session)
    deleted = await service.delete_datasource(datasource_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    return {"status": "ok"}
