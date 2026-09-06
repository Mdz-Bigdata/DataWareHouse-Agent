from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.params import Depends
from starlette.responses import StreamingResponse

from app.agent.dependencies import get_query_service, get_query_trace_repository
from app.api.deps import get_current_user
from app.api.schemas.query_schema import QuerySchema
from app.api.schemas.trace_schema import (
    DeepAnalysisItem,
    TraceFeedbackCreate,
    TraceFeedbackItem,
    TraceItem,
    TraceRegenerateRequest,
)
from app.conf.app_config import app_config
from app.core.rate_limit import limiter, rate_limit_query
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.query_feedback_repository import QueryFeedbackRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.repositories.mysql.verified_query_repository import VerifiedQueryRepository
from app.services.access_policy import (
    AccessPolicyContextV1,
    AccessPolicyError,
    resolve_access_policy,
)
from app.services.deep_analysis_service import DeepAnalysisService
from app.services.query_feedback_service import QueryFeedbackService
from app.services.query_service import ConversationContextError, QueryService

query_router = APIRouter(tags=["提问管理模块"])


def _resolve_access_policy_context(user: UserMySQL) -> AccessPolicyContextV1:
    """Resolve authorization once at the API boundary and fail closed."""

    try:
        return resolve_access_policy(
            user,
            domain="audio",
            datasource=app_config.db_dw.database,
        )
    except AccessPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@query_router.post("/api/query")
@limiter.limit(rate_limit_query())
async def query_answer(
    request: Request,
    query_schema: QuerySchema,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
):
    """
    web层负责处理前端请求，调用业务逻辑层，将业务层返回结果给到前端
    问题1：FastAPI如何进行流式响应
    问题2：业务层中需要客户端对象不是每次都创建对象 改为：项目启动进行客户端初始化 项目关闭执行客户端关闭
    问题3：依赖目前随用随时创建对象
    :param request: FastAPI 请求对象（slowapi 限流装饰器要求）
    :param query: 查询对象
    :param current_user: 当前登录用户，trace 属主隔离
    :return: 后端实时返回数据给前端
    """
    access_policy = _resolve_access_policy_context(current_user)
    await _validate_conversation_request(query_service, query_schema, current_user.id)
    return StreamingResponse(
        query_service.query(
            query_schema.query,
            parameters=query_schema.parameters,
            conversation_id=query_schema.conversation_id,
            parent_trace_id=query_schema.parent_trace_id,
            user_id=current_user.id,
            access_policy=access_policy,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@query_router.post("/api/query/sync")
@limiter.limit(rate_limit_query())
async def query_answer_sync(
    request: Request,
    query_schema: QuerySchema,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
):
    """Run the same query pipeline and return its final structured result."""

    access_policy = _resolve_access_policy_context(current_user)
    await _validate_conversation_request(query_service, query_schema, current_user.id)
    return await query_service.query_sync(
        query_schema.query,
        parameters=query_schema.parameters,
        conversation_id=query_schema.conversation_id,
        parent_trace_id=query_schema.parent_trace_id,
        user_id=current_user.id,
        access_policy=access_policy,
    )


@query_router.get("/api/traces", response_model=list[TraceItem])
async def list_traces(
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    """当前用户的查询记录（最新 50 条）。属主过滤，不存在跨用户读取入口。"""

    traces = await query_trace_repository.list_for_user(current_user.id, limit=50)
    return [
        TraceItem(
            id=trace.id,
            query_text=trace.query_text,
            status=trace.status,
            total_duration_ms=trace.total_duration_ms,
            started_at=trace.started_at,
            completed_at=trace.completed_at,
            conversation_id=trace.conversation_id,
            parent_trace_id=trace.parent_trace_id,
            regenerate_of_trace_id=trace.regenerate_of_trace_id,
            standalone_question=trace.standalone_question,
        )
        for trace in traces
    ]


@query_router.delete("/api/traces")
async def clear_traces(
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    """清空当前用户的查询记录，仅影响本人数据。"""

    deleted = await query_trace_repository.delete_for_user(current_user.id)
    return {"deleted": deleted}


@query_router.post(
    "/api/traces/{trace_id}/feedback",
    response_model=TraceFeedbackItem,
    status_code=status.HTTP_201_CREATED,
)
async def submit_trace_feedback(
    trace_id: str,
    body: TraceFeedbackCreate,
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    """Record one owner-scoped verdict without ever persisting result rows."""

    access_policy = _resolve_access_policy_context(current_user)
    session = query_trace_repository.session
    service = QueryFeedbackService(
        QueryFeedbackRepository(session),
        VerifiedQueryRepository(session),
    )
    try:
        feedback = await service.submit(
            trace_id=trace_id,
            user_id=current_user.id,
            verdict=body.verdict,
            reasons=list(body.reasons),
            comment=body.comment,
            domain=access_policy.domain,
            datasource=access_policy.datasource,
            row_level_scope=access_policy.row_level_scope(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TraceFeedbackItem(**feedback.__dict__)


@query_router.post("/api/traces/{trace_id}/regenerate")
@limiter.limit(rate_limit_query())
async def regenerate_trace(
    request: Request,
    trace_id: str,
    body: TraceRegenerateRequest,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    query_trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    """Create a sibling branch for an existing conversation trace without overwriting it."""

    source = await query_trace_repository.get_for_user(trace_id, current_user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    if source.conversation_id is None:
        raise HTTPException(status_code=409, detail="单轮查询不能重生成会话分支")
    try:
        await query_service.validate_conversation_context(
            user_id=current_user.id,
            conversation_id=source.conversation_id,
            parent_trace_id=source.parent_trace_id,
            regenerate_of_trace_id=source.id,
        )
    except ConversationContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    access_policy = _resolve_access_policy_context(current_user)
    return StreamingResponse(
        query_service.query(
            source.query_text,
            parameters=body.parameters,
            conversation_id=source.conversation_id,
            parent_trace_id=source.parent_trace_id,
            regenerate_of_trace_id=source.id,
            user_id=current_user.id,
            access_policy=access_policy,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@query_router.post(
    "/api/traces/{trace_id}/analysis",
    response_model=DeepAnalysisItem,
)
@limiter.limit(rate_limit_query())
async def analyze_trace(
    request: Request,
    trace_id: str,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    query_trace_repository: Annotated[
        QueryTraceRepository, Depends(get_query_trace_repository)
    ],
):
    """Re-authorize and rerun a bounded original query without storing its rows."""

    access_policy = _resolve_access_policy_context(current_user)
    try:
        return await DeepAnalysisService(
            query_service.dw_mysql_repository,
            query_service.meta_mysql_repository,
            query_trace_repository,
        ).analyze(
            source_trace_id=trace_id,
            user_id=current_user.id,
            access_policy=access_policy,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _validate_conversation_request(
    query_service: QueryService,
    query_schema: QuerySchema,
    user_id: str,
) -> None:
    try:
        await query_service.validate_conversation_context(
            user_id=user_id,
            conversation_id=query_schema.conversation_id,
            parent_trace_id=query_schema.parent_trace_id,
        )
    except ConversationContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
