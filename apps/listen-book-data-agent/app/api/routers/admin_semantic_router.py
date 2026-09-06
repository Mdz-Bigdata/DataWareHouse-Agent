from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import (
    get_column_qdrant_repository,
    get_dw_session,
    get_embedding_client,
    get_meta_mysql_repository,
    get_meta_session,
    get_metric_qdrant_repository,
    get_value_es_repository,
)
from app.api.deps import require_admin
from app.api.schemas.semantic_schema import (
    DatasourceTestRequest,
    DatasourceTestResult,
    SemanticColumnItem,
    SemanticColumnUpdate,
    SemanticMetricCreate,
    SemanticMetricItem,
    SemanticMetricUpsert,
    SemanticOverview,
    SemanticRelationshipCreate,
    SemanticRelationshipItem,
    SemanticRelationshipUpsert,
    SemanticReleaseActivateRequest,
    SemanticReleaseItem,
    SemanticTableItem,
    SemanticTableUpdate,
)
from app.conf.app_config import app_config
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.services.governance_audit_service import GovernanceAuditService
from app.services.knowledge_rebuild_service import rebuild_status, start_rebuild
from app.services.recall_test_service import recall_test
from app.services.semantic_admin_service import (
    SemanticAdminService,
    datasource_infos,
    test_datasource,
)
from app.services.semantic_release_service import (
    SemanticReleaseError,
    SemanticReleaseService,
    list_semantic_release_items,
)

admin_semantic_router = APIRouter(tags=["语义层管理"])


def _to_table_item(table) -> SemanticTableItem:
    return SemanticTableItem(
        id=table.id,
        name=table.name,
        role=table.role,
        description=table.description,
        alias=table.alias,
        domain=table.domain,
    )


def _to_column_item(column) -> SemanticColumnItem:
    return SemanticColumnItem(
        id=column.id,
        table_id=column.table_id,
        name=column.name,
        type=column.type,
        role=column.role,
        description=column.description,
        alias=column.alias,
        examples=column.examples,
        nullable=column.nullable,
        sensitive=column.sensitive,
        sync=column.sync,
        enum_values=column.enum_values,
    )


def _to_metric_item(metric) -> SemanticMetricItem:
    return SemanticMetricItem(
        id=metric.id,
        name=metric.name,
        description=metric.description,
        alias=metric.alias,
        formula=metric.formula,
        relevant_columns=metric.relevant_columns,
        filters=metric.filters,
        time_column=metric.time_column,
        unit=metric.unit,
        dimensions=metric.dimensions,
        snapshot=metric.snapshot,
    )


def _to_relationship_item(relationship) -> SemanticRelationshipItem:
    return SemanticRelationshipItem(
        id=relationship.id,
        source_table=relationship.source_table,
        source_column=relationship.source_column,
        target_table=relationship.target_table,
        target_column=relationship.target_column,
        relationship_type=relationship.relationship_type,
        condition=relationship.condition,
        physical=relationship.physical,
    )


@admin_semantic_router.get(
    "/api/admin/semantic/overview", response_model=SemanticOverview
)
async def semantic_overview(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = SemanticAdminService(meta_session)
    data = await service.overview()
    return SemanticOverview(**data, datasources=datasource_infos())


@admin_semantic_router.post(
    "/api/admin/semantic/datasources/test", response_model=DatasourceTestResult
)
async def datasource_test(
    body: DatasourceTestRequest,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    dw_session: Annotated[AsyncSession, Depends(get_dw_session)],
):
    session = meta_session if body.target == "meta" else dw_session
    ok, latency_ms, error = await test_datasource(session)
    return DatasourceTestResult(ok=ok, latency_ms=latency_ms, error=error)


@admin_semantic_router.get(
    "/api/admin/semantic/tables", response_model=list[SemanticTableItem]
)
async def list_tables(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    tables = await SemanticAdminService(meta_session).list_tables()
    return [_to_table_item(table) for table in tables]


@admin_semantic_router.put(
    "/api/admin/semantic/tables/{table_id}", response_model=SemanticTableItem
)
async def update_table(
    table_id: str,
    body: SemanticTableUpdate,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    table = await SemanticAdminService(meta_session).update_table(table_id, body)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="表不存在")
    return _to_table_item(table)


@admin_semantic_router.get(
    "/api/admin/semantic/tables/{table_id}/columns",
    response_model=list[SemanticColumnItem],
)
async def list_columns(
    table_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    columns = await SemanticAdminService(meta_session).list_columns(table_id)
    return [_to_column_item(column) for column in columns]


@admin_semantic_router.put(
    "/api/admin/semantic/columns/{column_id}", response_model=SemanticColumnItem
)
async def update_column(
    column_id: str,
    body: SemanticColumnUpdate,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    column = await SemanticAdminService(meta_session).update_column(column_id, body)
    if column is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="字段不存在")
    return _to_column_item(column)


@admin_semantic_router.get(
    "/api/admin/semantic/metrics", response_model=list[SemanticMetricItem]
)
async def list_metrics(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    metrics = await SemanticAdminService(meta_session).list_metrics()
    return [_to_metric_item(metric) for metric in metrics]


@admin_semantic_router.post(
    "/api/admin/semantic/metrics",
    response_model=SemanticMetricItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_metric(
    body: SemanticMetricCreate,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        metric = await SemanticAdminService(meta_session).create_metric(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _to_metric_item(metric)


@admin_semantic_router.put(
    "/api/admin/semantic/metrics/{metric_id}", response_model=SemanticMetricItem
)
async def update_metric(
    metric_id: str,
    body: SemanticMetricUpsert,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    metric = await SemanticAdminService(meta_session).update_metric(metric_id, body)
    if metric is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指标不存在")
    return _to_metric_item(metric)


@admin_semantic_router.delete("/api/admin/semantic/metrics/{metric_id}")
async def delete_metric(
    metric_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    deleted = await SemanticAdminService(meta_session).delete_metric(metric_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指标不存在")
    return {"status": "ok"}


@admin_semantic_router.get(
    "/api/admin/semantic/relationships", response_model=list[SemanticRelationshipItem]
)
async def list_relationships(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    relationships = await SemanticAdminService(meta_session).list_relationships()
    return [_to_relationship_item(relationship) for relationship in relationships]


@admin_semantic_router.post(
    "/api/admin/semantic/relationships",
    response_model=SemanticRelationshipItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_relationship(
    body: SemanticRelationshipCreate,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        relationship = await SemanticAdminService(meta_session).create_relationship(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _to_relationship_item(relationship)


@admin_semantic_router.put(
    "/api/admin/semantic/relationships/{relationship_id}",
    response_model=SemanticRelationshipItem,
)
async def update_relationship(
    relationship_id: str,
    body: SemanticRelationshipUpsert,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    relationship = await SemanticAdminService(meta_session).update_relationship(
        relationship_id, body
    )
    if relationship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联关系不存在")
    return _to_relationship_item(relationship)


@admin_semantic_router.delete("/api/admin/semantic/relationships/{relationship_id}")
async def delete_relationship(
    relationship_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    deleted = await SemanticAdminService(meta_session).delete_relationship(relationship_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关联关系不存在")
    return {"status": "ok"}


class RecallTestRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@admin_semantic_router.post("/api/admin/semantic/recall-test")
async def recall_test_endpoint(
    body: RecallTestRequest,
    _: Annotated[UserMySQL, Depends(require_admin)],
    embedding_client: Annotated[object, Depends(get_embedding_client)],
    column_qdrant_repository: Annotated[object, Depends(get_column_qdrant_repository)],
    metric_qdrant_repository: Annotated[object, Depends(get_metric_qdrant_repository)],
    meta_mysql_repository: Annotated[object, Depends(get_meta_mysql_repository)],
):
    """输入自然语言问题，返回召回到的表、字段、指标（不执行 SQL 生成）。"""
    return await recall_test(
        body.question,
        embedding_client=embedding_client,
        column_qdrant_repository=column_qdrant_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        meta_mysql_repository=meta_mysql_repository,
    )


@admin_semantic_router.post("/api/admin/semantic/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_endpoint(_: Annotated[UserMySQL, Depends(require_admin)]):
    """后台重建检索索引（以 MySQL 当前元数据为源）。已有任务运行时返回 409。"""
    started = await start_rebuild()
    if not started:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="已有重建任务正在执行"
        )
    return {"status": "started"}


@admin_semantic_router.get("/api/admin/semantic/rebuild/status")
async def rebuild_status_endpoint(_: Annotated[UserMySQL, Depends(require_admin)]):
    return rebuild_status()


@admin_semantic_router.get(
    "/api/admin/semantic/releases",
    response_model=list[SemanticReleaseItem],
)
async def list_semantic_releases(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    domain: str = "audio",
    datasource: str | None = None,
):
    return await list_semantic_release_items(
        meta_session,
        domain=domain,
        datasource=datasource or app_config.db_dw.database,
    )


@admin_semantic_router.post(
    "/api/admin/semantic/releases/activate",
    response_model=SemanticReleaseItem,
)
async def activate_semantic_release(
    body: SemanticReleaseActivateRequest,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    column_repository=Depends(get_column_qdrant_repository),
    metric_repository=Depends(get_metric_qdrant_repository),
    value_repository=Depends(get_value_es_repository),
):
    datasource = body.datasource or app_config.db_dw.database
    service = SemanticReleaseService(
        meta_repository=MetaMySQlRepository(meta_session),
        column_repository=column_repository,
        metric_repository=metric_repository,
        value_repository=value_repository,
    )
    try:
        release = await service.activate(
            build_id=body.build_id,
            query_set_id=body.query_set_id,
            domain=body.domain,
            datasource=datasource,
            created_by=current_user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SemanticReleaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="activate",
        resource_type="semantic_release",
        resource_id=release.id,
        details={"version": release.version, "build_id": release.knowledge_build_id},
    )
    items = await service.list_release_items(domain=body.domain, datasource=datasource)
    return next(item for item in items if item["id"] == release.id)


@admin_semantic_router.post(
    "/api/admin/semantic/releases/{release_id}/rollback",
    response_model=SemanticReleaseItem,
)
async def rollback_semantic_release(
    release_id: str,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    column_repository=Depends(get_column_qdrant_repository),
    metric_repository=Depends(get_metric_qdrant_repository),
    value_repository=Depends(get_value_es_repository),
):
    service = SemanticReleaseService(
        meta_repository=MetaMySQlRepository(meta_session),
        column_repository=column_repository,
        metric_repository=metric_repository,
        value_repository=value_repository,
    )
    try:
        release = await service.rollback(release_id, created_by=current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SemanticReleaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="rollback",
        resource_type="semantic_release",
        resource_id=release.id,
        details={"source_release_id": release_id, "version": release.version},
    )
    items = await service.list_release_items(
        domain=release.domain,
        datasource=release.datasource,
    )
    return next(item for item in items if item["id"] == release.id)
