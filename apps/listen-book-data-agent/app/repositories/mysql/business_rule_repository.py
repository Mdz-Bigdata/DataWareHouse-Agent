from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.business_rule import BusinessRuleRevision
from app.models.mysql.business_rule_mysql import BusinessRuleRevisionMySQL
from app.models.mysql.semantic_release_mysql import (
    BusinessRuleSetVersionMySQL,
    SemanticReleaseMySQL,
)
from app.repositories.mysql.semantic_release_repository import SemanticReleaseRepository


class BusinessRuleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, rule_id: str) -> BusinessRuleRevisionMySQL | None:
        return await self.session.get(BusinessRuleRevisionMySQL, rule_id)

    async def next_version(self, *, rule_key: str, domain: str, datasource: str) -> int:
        result = await self.session.execute(
            select(func.max(BusinessRuleRevisionMySQL.version)).where(
                BusinessRuleRevisionMySQL.rule_key == rule_key,
                BusinessRuleRevisionMySQL.domain == domain,
                BusinessRuleRevisionMySQL.datasource == datasource,
            )
        )
        return int(result.scalar_one_or_none() or 0) + 1

    async def add(self, row: BusinessRuleRevisionMySQL) -> None:
        self.session.add(row)
        await self.session.flush()

    async def list_for_scope(
        self,
        *,
        domain: str,
        datasource: str,
        status: str | None = None,
    ) -> list[BusinessRuleRevisionMySQL]:
        statement = select(BusinessRuleRevisionMySQL).where(
            BusinessRuleRevisionMySQL.domain == domain,
            BusinessRuleRevisionMySQL.datasource == datasource,
        )
        if status is not None:
            statement = statement.where(BusinessRuleRevisionMySQL.status == status)
        result = await self.session.execute(
            statement.order_by(
                BusinessRuleRevisionMySQL.priority.desc(),
                BusinessRuleRevisionMySQL.rule_key,
                BusinessRuleRevisionMySQL.version.desc(),
            )
        )
        return list(result.scalars().all())

    async def disable_other_published_revisions(
        self, selected: BusinessRuleRevisionMySQL
    ) -> None:
        result = await self.session.execute(
            select(BusinessRuleRevisionMySQL).where(
                BusinessRuleRevisionMySQL.rule_key == selected.rule_key,
                BusinessRuleRevisionMySQL.domain == selected.domain,
                BusinessRuleRevisionMySQL.datasource == selected.datasource,
                BusinessRuleRevisionMySQL.status == "published",
                BusinessRuleRevisionMySQL.id != selected.id,
            )
        )
        for row in result.scalars().all():
            row.status = "disabled"

    async def list_effective_for_scope(
        self,
        *,
        domain: str,
        datasource: str,
    ) -> tuple[
        list[BusinessRuleRevisionMySQL],
        SemanticReleaseMySQL | None,
        BusinessRuleSetVersionMySQL | None,
    ]:
        """Resolve release-pinned rules, with legacy fallback before releases."""

        release_repository = SemanticReleaseRepository(self.session)
        release = await release_repository.get_active_release(
            domain=domain,
            datasource=datasource,
        )
        if release is None:
            return (
                await self.list_for_scope(
                    domain=domain,
                    datasource=datasource,
                    status="published",
                ),
                None,
                None,
            )
        rule_set = await release_repository.get_rule_set(
            release.business_rule_set_id
        )
        if rule_set is None:
            return [], release, None
        revision_ids = [
            str(item["revision_id"])
            for item in rule_set.manifest
            if item.get("revision_id")
        ]
        if not revision_ids:
            return [], release, rule_set
        rows = await self.session.scalars(
            select(BusinessRuleRevisionMySQL).where(
                BusinessRuleRevisionMySQL.id.in_(revision_ids),
                BusinessRuleRevisionMySQL.domain == domain,
                BusinessRuleRevisionMySQL.datasource == datasource,
            )
        )
        by_id = {row.id: row for row in rows}
        return (
            [by_id[revision_id] for revision_id in revision_ids if revision_id in by_id],
            release,
            rule_set,
        )


def business_rule_to_entity(row: BusinessRuleRevisionMySQL) -> BusinessRuleRevision:
    return BusinessRuleRevision(
        id=row.id,
        rule_key=row.rule_key,
        version=row.version,
        rule_type=row.rule_type,
        content=row.content,
        domain=row.domain,
        datasource=row.datasource,
        intents=list(row.intents or []),
        semantic_ids=list(row.semantic_ids or []),
        priority=row.priority,
        status=row.status,
        created_by=row.created_by,
        reviewer_id=row.reviewer_id,
    )
