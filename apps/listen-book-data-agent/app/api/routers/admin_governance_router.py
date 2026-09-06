from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.agent.dependencies import get_embedding_client, get_meta_session, get_qdrant_client
from app.api.deps import require_admin
from app.api.schemas.governance_schema import (
    BusinessRuleCreate,
    BusinessRuleItem,
    BusinessRuleReview,
    QuerySetItem,
    QuerySetPublishRequest,
    SemanticTermCreate,
    SemanticTermItem,
    VerifiedQueryCreate,
    VerifiedQueryItem,
    VerifiedQueryReview,
)
from app.conf.app_config import app_config
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.business_rule_repository import (
    BusinessRuleRepository,
    business_rule_to_entity,
)
from app.repositories.mysql.semantic_term_repository import (
    SemanticTermRepository,
)
from app.repositories.mysql.semantic_term_repository import (
    to_entity as term_to_entity,
)
from app.repositories.mysql.verified_query_repository import (
    QuerySetRepository,
    VerifiedQueryRepository,
    query_set_to_entity,
    revision_to_entity,
)
from app.repositories.qdrant.semantic_term_qdrant_repository import (
    SemanticTermQdrantRepository,
)
from app.repositories.qdrant.verified_query_qdrant_repository import (
    VerifiedQueryQdrantRepository,
)
from app.services.business_rule_service import BusinessRuleService
from app.services.governance_audit_service import GovernanceAuditService
from app.services.query_set_service import QuerySetService
from app.services.semantic_term_service import SemanticTermService
from app.services.verified_query_service import VerifiedQueryService

admin_governance_router = APIRouter(tags=["语义治理"])
_SEED_PATH = Path(__file__).parents[3] / "conf" / "domains" / "audio" / "queries.yaml"


def _datasource(value: str | None) -> str:
    return value or app_config.db_dw.database


def _term_service(session, qdrant_client, embedding_client) -> SemanticTermService:
    return SemanticTermService(
        SemanticTermRepository(session),
        SemanticTermQdrantRepository(qdrant_client),
        embedding_client,
    )


@admin_governance_router.get("/api/admin/terms", response_model=list[SemanticTermItem])
async def list_terms(
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    domain: str = "audio",
    datasource: str | None = None,
):
    rows = await SemanticTermRepository(meta_session).list_for_scope(
        domain=domain,
        datasource=_datasource(datasource),
    )
    return [SemanticTermItem(**term_to_entity(row).__dict__) for row in rows]


@admin_governance_router.post(
    "/api/admin/terms",
    response_model=SemanticTermItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_term(
    body: SemanticTermCreate,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    qdrant_client=Depends(get_qdrant_client),
    embedding_client=Depends(get_embedding_client),
):
    term = await _term_service(meta_session, qdrant_client, embedding_client).create_draft(
        term_key=body.term_key,
        standard_term=body.standard_term,
        synonyms=body.synonyms,
        description=body.description,
        bindings=[item.model_dump() for item in body.bindings],
        domain=body.domain,
        datasource=_datasource(body.datasource),
        created_by=current_user.id,
    )
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="create_candidate",
        resource_type="semantic_term",
        resource_id=term.id,
        details={"term_key": term.term_key, "version": term.version},
    )
    return SemanticTermItem(**term.__dict__)


@admin_governance_router.post("/api/admin/terms/{term_id}/publish", response_model=SemanticTermItem)
async def publish_term(
    term_id: str,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    qdrant_client=Depends(get_qdrant_client),
    embedding_client=Depends(get_embedding_client),
):
    try:
        term = await _term_service(meta_session, qdrant_client, embedding_client).publish(term_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="publish",
        resource_type="semantic_term",
        resource_id=term.id,
        details={"term_key": term.term_key, "version": term.version},
    )
    return SemanticTermItem(**term.__dict__)


@admin_governance_router.post("/api/admin/terms/{term_id}/disable", response_model=SemanticTermItem)
async def disable_term(
    term_id: str,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    qdrant_client=Depends(get_qdrant_client),
    embedding_client=Depends(get_embedding_client),
):
    try:
        term = await _term_service(meta_session, qdrant_client, embedding_client).disable(term_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="disable",
        resource_type="semantic_term",
        resource_id=term.id,
        details={"term_key": term.term_key, "version": term.version},
    )
    return SemanticTermItem(**term.__dict__)


@admin_governance_router.get("/api/admin/verified-queries", response_model=list[VerifiedQueryItem])
async def list_verified_queries(
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    domain: str = "audio",
    datasource: str | None = None,
    lifecycle: str | None = None,
):
    rows = await VerifiedQueryRepository(meta_session).list_revisions(
        domain=domain,
        datasource=_datasource(datasource),
        lifecycle=lifecycle,
    )
    return [VerifiedQueryItem(**revision_to_entity(row).__dict__) for row in rows]


@admin_governance_router.post(
    "/api/admin/verified-queries",
    response_model=VerifiedQueryItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_verified_query(
    body: VerifiedQueryCreate,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    revision = await VerifiedQueryService(VerifiedQueryRepository(meta_session)).create_revision(
        case_key=body.case_key,
        question=body.question,
        dialect=body.dialect,
        sql_template=body.sql_template,
        parameter_schema=[item.model_dump() for item in body.parameter_schema],
        expected_fields=body.expected_fields,
        expected_metrics=body.expected_metrics,
        assertions=body.assertions,
        domain=body.domain,
        datasource=_datasource(body.datasource),
        source_trace_id=body.source_trace_id,
        source=body.source,
        created_by=current_user.id,
    )
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="create_candidate",
        resource_type="verified_query_revision",
        resource_id=revision.id,
        details={"case_key": revision.case_key, "revision": revision.revision},
    )
    return VerifiedQueryItem(**revision.__dict__)


@admin_governance_router.post(
    "/api/admin/verified-queries/{revision_id}/review",
    response_model=VerifiedQueryItem,
)
async def review_verified_query(
    revision_id: str,
    body: VerifiedQueryReview,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        revision = await VerifiedQueryService(VerifiedQueryRepository(meta_session)).review(
            revision_id, reviewer_id=current_user.id, approved=body.approved
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="approve" if body.approved else "reject",
        resource_type="verified_query_revision",
        resource_id=revision.id,
        details={"case_key": revision.case_key, "revision": revision.revision},
    )
    return VerifiedQueryItem(**revision.__dict__)


@admin_governance_router.post("/api/admin/verified-queries/import-seeds")
async def import_verified_query_seeds(
    body: QuerySetPublishRequest,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = QuerySetService(
        VerifiedQueryRepository(meta_session), QuerySetRepository(meta_session)
    )
    imported = await service.import_seed_file(
        _SEED_PATH,
        domain=body.domain,
        datasource=_datasource(body.datasource),
        created_by=current_user.id,
    )
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="import_seeds",
        resource_type="verified_query_revision",
        resource_id=body.domain,
        details={"imported_count": len(imported)},
    )
    return {"imported": imported, "count": len(imported)}


@admin_governance_router.get("/api/admin/query-sets", response_model=list[QuerySetItem])
async def list_query_sets(
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    domain: str = "audio",
    datasource: str | None = None,
):
    rows = await QuerySetRepository(meta_session).list_versions(
        domain=domain, datasource=_datasource(datasource)
    )
    return [QuerySetItem(**query_set_to_entity(row).__dict__) for row in rows]


@admin_governance_router.post("/api/admin/query-sets/publish", response_model=QuerySetItem)
async def publish_query_set(
    body: QuerySetPublishRequest,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    qdrant_client=Depends(get_qdrant_client),
    embedding_client=Depends(get_embedding_client),
):
    service = QuerySetService(
        VerifiedQueryRepository(meta_session),
        QuerySetRepository(meta_session),
        VerifiedQueryQdrantRepository(qdrant_client),
        embedding_client,
    )
    try:
        query_set = await service.publish(
            domain=body.domain,
            datasource=_datasource(body.datasource),
            created_by=current_user.id,
            reviewer_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="publish",
        resource_type="query_set_version",
        resource_id=query_set.id,
        details={"version": query_set.version, "content_hash": query_set.content_hash},
    )
    return QuerySetItem(**query_set.__dict__)


@admin_governance_router.get("/api/admin/query-sets/{query_set_id}/export")
async def export_query_set(
    query_set_id: str,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = QuerySetService(
        VerifiedQueryRepository(meta_session), QuerySetRepository(meta_session)
    )
    try:
        content = await service.export_yaml(query_set_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=content, media_type="application/yaml")


@admin_governance_router.get("/api/admin/business-rules", response_model=list[BusinessRuleItem])
async def list_business_rules(
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    domain: str = "audio",
    datasource: str | None = None,
    rule_status: str | None = None,
):
    rows = await BusinessRuleRepository(meta_session).list_for_scope(
        domain=domain,
        datasource=_datasource(datasource),
        status=rule_status,
    )
    return [BusinessRuleItem(**business_rule_to_entity(row).__dict__) for row in rows]


@admin_governance_router.post(
    "/api/admin/business-rules",
    response_model=BusinessRuleItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_business_rule(
    body: BusinessRuleCreate,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        rule = await BusinessRuleService(BusinessRuleRepository(meta_session)).create_draft(
            rule_key=body.rule_key,
            rule_type=body.rule_type,
            content=body.content,
            domain=body.domain,
            datasource=_datasource(body.datasource),
            intents=list(body.intents),
            semantic_ids=body.semantic_ids,
            priority=body.priority,
            created_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="create_draft",
        resource_type="business_rule_revision",
        resource_id=rule.id,
        details={"rule_key": rule.rule_key, "version": rule.version},
    )
    return BusinessRuleItem(**rule.__dict__)


@admin_governance_router.post(
    "/api/admin/business-rules/{rule_id}/review",
    response_model=BusinessRuleItem,
)
async def review_business_rule(
    rule_id: str,
    body: BusinessRuleReview,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        rule = await BusinessRuleService(BusinessRuleRepository(meta_session)).review(
            rule_id,
            reviewer_id=current_user.id,
            approved=body.approved,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="approve" if body.approved else "reject",
        resource_type="business_rule_revision",
        resource_id=rule.id,
        details={"rule_key": rule.rule_key, "version": rule.version},
    )
    return BusinessRuleItem(**rule.__dict__)


@admin_governance_router.post(
    "/api/admin/business-rules/{rule_id}/publish",
    response_model=BusinessRuleItem,
)
async def publish_business_rule(
    rule_id: str,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        rule = await BusinessRuleService(BusinessRuleRepository(meta_session)).publish(rule_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="publish",
        resource_type="business_rule_revision",
        resource_id=rule.id,
        details={"rule_key": rule.rule_key, "version": rule.version},
    )
    return BusinessRuleItem(**rule.__dict__)


@admin_governance_router.post(
    "/api/admin/business-rules/{rule_id}/disable",
    response_model=BusinessRuleItem,
)
async def disable_business_rule(
    rule_id: str,
    current_user: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    try:
        rule = await BusinessRuleService(BusinessRuleRepository(meta_session)).disable(rule_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await GovernanceAuditService(meta_session).record(
        actor_id=current_user.id,
        action="disable",
        resource_type="business_rule_revision",
        resource_id=rule.id,
        details={"rule_key": rule.rule_key, "version": rule.version},
    )
    return BusinessRuleItem(**rule.__dict__)
