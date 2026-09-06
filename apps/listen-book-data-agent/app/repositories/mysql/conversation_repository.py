from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.conversation_mysql import ConversationMySQL
from app.models.mysql.query_trace_mysql import QueryTraceMySQL


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, conversation: ConversationMySQL) -> None:
        self.session.add(conversation)
        await self.session.flush()

    async def get_for_user(self, conversation_id: str, user_id: str) -> ConversationMySQL | None:
        result = await self.session.execute(
            select(ConversationMySQL).where(
                ConversationMySQL.id == conversation_id,
                ConversationMySQL.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: str,
        *,
        search: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[ConversationMySQL]:
        statement = select(ConversationMySQL).where(ConversationMySQL.user_id == user_id)
        if not include_archived:
            statement = statement.where(ConversationMySQL.status == "active")
        if search and search.strip():
            term = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    ConversationMySQL.title.like(term),
                    ConversationMySQL.id.in_(
                        select(QueryTraceMySQL.conversation_id).where(
                            QueryTraceMySQL.user_id == user_id,
                            QueryTraceMySQL.query_text.like(term),
                        )
                    ),
                )
            )
        result = await self.session.execute(
            statement.order_by(ConversationMySQL.updated_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_traces_for_user(
        self,
        conversation_id: str,
        user_id: str,
    ) -> list[QueryTraceMySQL]:
        result = await self.session.execute(
            select(QueryTraceMySQL)
            .where(
                QueryTraceMySQL.conversation_id == conversation_id,
                QueryTraceMySQL.user_id == user_id,
            )
            .order_by(QueryTraceMySQL.started_at, QueryTraceMySQL.id)
        )
        return list(result.scalars().all())

    async def touch(self, conversation: ConversationMySQL) -> None:
        conversation.updated_at = datetime.now()
        await self.session.commit()

    async def update(
        self,
        conversation: ConversationMySQL,
        *,
        title: str | None = None,
        status: str | None = None,
    ) -> None:
        if title is not None:
            conversation.title = title
        if status is not None:
            conversation.status = status
        conversation.updated_at = datetime.now()
        await self.session.commit()
