from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.insight_card_mysql import InsightCardMySQL


class InsightCardRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, card: InsightCardMySQL) -> None:
        self.session.add(card)
        await self.session.flush()

    async def get_for_user(self, card_id: str, user_id: str) -> InsightCardMySQL | None:
        result = await self.session.execute(
            select(InsightCardMySQL).where(
                InsightCardMySQL.id == card_id,
                InsightCardMySQL.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[InsightCardMySQL]:
        result = await self.session.execute(
            select(InsightCardMySQL)
            .where(InsightCardMySQL.user_id == user_id)
            .order_by(InsightCardMySQL.created_at.desc(), InsightCardMySQL.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete_for_user(self, card_id: str, user_id: str) -> bool:
        result = await self.session.execute(
            delete(InsightCardMySQL).where(
                InsightCardMySQL.id == card_id,
                InsightCardMySQL.user_id == user_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)
