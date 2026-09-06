from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.params import Depends
from starlette.responses import StreamingResponse

from app.agent.dependencies import get_query_service, get_query_trace_repository
from app.api.deps import get_current_user
from app.api.schemas.insight_card_schema import InsightCardExecuteRequest, InsightCardItem
from app.conf.app_config import app_config
from app.core.rate_limit import limiter, rate_limit_query
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.insight_card_repository import InsightCardRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.access_policy import AccessPolicyError, resolve_access_policy
from app.services.insight_card_service import InsightCardService
from app.services.query_service import QueryService

insight_card_router = APIRouter(prefix="/api/insight-cards", tags=["洞察卡片"])


def _service(trace_repository: QueryTraceRepository) -> InsightCardService:
    return InsightCardService(
        InsightCardRepository(trace_repository.session),
        trace_repository,
    )


def _policy(user: UserMySQL):
    try:
        return resolve_access_policy(
            user,
            domain="audio",
            datasource=app_config.db_dw.database,
        )
    except AccessPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@insight_card_router.get("", response_model=list[InsightCardItem])
async def list_insight_cards(
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    return await InsightCardRepository(trace_repository.session).list_for_user(current_user.id)


@insight_card_router.post(
    "/from-trace/{trace_id}",
    response_model=InsightCardItem,
    status_code=status.HTTP_201_CREATED,
)
async def save_insight_card(
    trace_id: str,
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    policy = _policy(current_user)
    try:
        return await _service(trace_repository).save_from_trace(
            trace_id=trace_id,
            user_id=current_user.id,
            row_level_scope=policy.row_level_scope(),
            dialect=app_config.db_dw.dialect,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@insight_card_router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_insight_card(
    card_id: str,
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    deleted = await InsightCardRepository(trace_repository.session).delete_for_user(
        card_id, current_user.id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="洞察卡片不存在")


@insight_card_router.post("/{card_id}/execute")
@limiter.limit(rate_limit_query())
async def execute_insight_card(
    request: Request,
    card_id: str,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
    payload: InsightCardExecuteRequest | None = None,
):
    try:
        card = await _service(trace_repository).get_owned(card_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    policy = _policy(current_user)
    execute_request = payload or InsightCardExecuteRequest()
    return StreamingResponse(
        query_service.query(
            card.question,
            conversation_id=execute_request.conversation_id,
            parent_trace_id=execute_request.parent_trace_id,
            user_id=current_user.id,
            access_policy=policy,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
