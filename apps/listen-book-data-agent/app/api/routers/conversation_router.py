from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.params import Depends

from app.agent.dependencies import get_query_trace_repository
from app.api.deps import get_current_user
from app.api.schemas.conversation_schema import (
    ConversationCreate,
    ConversationItem,
    ConversationTurnItem,
    ConversationUpdate,
)
from app.models.mysql.conversation_mysql import ConversationMySQL
from app.models.mysql.user_mysql import UserMySQL
from app.repositories.mysql.conversation_repository import ConversationRepository
from app.repositories.mysql.query_trace_repository import QueryTraceRepository

conversation_router = APIRouter(prefix="/api/conversations", tags=["多轮会话"])


@conversation_router.get("", response_model=list[ConversationItem])
async def list_conversations(
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    include_archived: bool = False,
):
    repository = ConversationRepository(trace_repository.session)
    return await repository.list_for_user(
        current_user.id,
        search=search,
        include_archived=include_archived,
        limit=50,
    )


@conversation_router.post(
    "",
    response_model=ConversationItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    body: ConversationCreate,
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    repository = ConversationRepository(trace_repository.session)
    conversation = ConversationMySQL(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        title=body.title.strip(),
        status="active",
    )
    await repository.add(conversation)
    await repository.session.commit()
    await repository.session.refresh(conversation)
    return conversation


@conversation_router.patch("/{conversation_id}", response_model=ConversationItem)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    repository = ConversationRepository(trace_repository.session)
    conversation = await repository.get_for_user(conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    await repository.update(
        conversation,
        title=body.title.strip() if body.title is not None else None,
        status=body.status,
    )
    return conversation


@conversation_router.get(
    "/{conversation_id}/turns",
    response_model=list[ConversationTurnItem],
)
async def list_conversation_turns(
    conversation_id: str,
    current_user: Annotated[UserMySQL, Depends(get_current_user)],
    trace_repository: Annotated[QueryTraceRepository, Depends(get_query_trace_repository)],
):
    repository = ConversationRepository(trace_repository.session)
    conversation = await repository.get_for_user(conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return await repository.list_traces_for_user(conversation_id, current_user.id)
