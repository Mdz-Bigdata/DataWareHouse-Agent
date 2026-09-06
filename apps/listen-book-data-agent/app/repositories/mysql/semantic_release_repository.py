from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.semantic_release_mysql import (
    ActiveSemanticReleaseMySQL,
    BusinessRuleSetVersionMySQL,
    SemanticReleaseMySQL,
)


class SemanticReleaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_release_version(self, *, domain: str, datasource: str) -> int:
        value = await self.session.scalar(
            select(func.max(SemanticReleaseMySQL.version)).where(
                SemanticReleaseMySQL.domain == domain,
                SemanticReleaseMySQL.datasource == datasource,
            )
        )
        return int(value or 0) + 1

    async def next_rule_set_version(self, *, domain: str, datasource: str) -> int:
        value = await self.session.scalar(
            select(func.max(BusinessRuleSetVersionMySQL.version)).where(
                BusinessRuleSetVersionMySQL.domain == domain,
                BusinessRuleSetVersionMySQL.datasource == datasource,
            )
        )
        return int(value or 0) + 1

    async def get_release(self, release_id: str) -> SemanticReleaseMySQL | None:
        return await self.session.get(SemanticReleaseMySQL, release_id)

    async def get_rule_set(
        self, rule_set_id: str
    ) -> BusinessRuleSetVersionMySQL | None:
        return await self.session.get(BusinessRuleSetVersionMySQL, rule_set_id)

    async def get_rule_set_by_hash(
        self, content_hash: str
    ) -> BusinessRuleSetVersionMySQL | None:
        return await self.session.scalar(
            select(BusinessRuleSetVersionMySQL).where(
                BusinessRuleSetVersionMySQL.content_hash == content_hash
            )
        )

    async def add_rule_set(self, row: BusinessRuleSetVersionMySQL) -> None:
        self.session.add(row)
        await self.session.flush()

    async def add_release(self, row: SemanticReleaseMySQL) -> None:
        self.session.add(row)
        await self.session.flush()

    async def get_active_release(
        self,
        *,
        domain: str,
        datasource: str,
    ) -> SemanticReleaseMySQL | None:
        statement = (
            select(SemanticReleaseMySQL)
            .join(
                ActiveSemanticReleaseMySQL,
                ActiveSemanticReleaseMySQL.release_id == SemanticReleaseMySQL.id,
            )
            .where(
                ActiveSemanticReleaseMySQL.domain == domain,
                ActiveSemanticReleaseMySQL.datasource == datasource,
            )
        )
        return await self.session.scalar(statement)

    async def set_active_release(
        self,
        *,
        domain: str,
        datasource: str,
        release_id: str,
    ) -> None:
        key = {"domain": domain, "datasource": datasource}
        row = await self.session.get(ActiveSemanticReleaseMySQL, key)
        if row is None:
            self.session.add(
                ActiveSemanticReleaseMySQL(
                    domain=domain,
                    datasource=datasource,
                    release_id=release_id,
                )
            )
        else:
            row.release_id = release_id
            row.updated_at = datetime.now()
        await self.session.flush()

    async def list_releases(
        self,
        *,
        domain: str,
        datasource: str,
    ) -> list[SemanticReleaseMySQL]:
        rows = await self.session.scalars(
            select(SemanticReleaseMySQL)
            .where(
                SemanticReleaseMySQL.domain == domain,
                SemanticReleaseMySQL.datasource == datasource,
            )
            .order_by(SemanticReleaseMySQL.version.desc())
        )
        return list(rows)
