"""Atomic semantic release activation with compensated external alias switches."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.knowledge_build_mysql import (
    KnowledgeBuildMySQL,
    KnowledgeBuildValidationMySQL,
)
from app.models.mysql.semantic_release_mysql import (
    BusinessRuleSetVersionMySQL,
    SemanticReleaseMySQL,
)
from app.repositories.mysql.business_rule_repository import BusinessRuleRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.mysql.semantic_release_repository import (
    SemanticReleaseRepository,
)
from app.repositories.mysql.verified_query_repository import QuerySetRepository


class SemanticReleaseError(ValueError):
    """Release precondition or compensated activation failure."""


class SemanticReleaseRecoveryError(SemanticReleaseError):
    """External aliases could not be restored after a failed DB activation."""


class SemanticReleaseService:
    def __init__(
        self,
        *,
        meta_repository: MetaMySQlRepository,
        column_repository: object,
        metric_repository: object,
        value_repository: object,
    ):
        self.meta_repository = meta_repository
        self.session = meta_repository.session
        self.release_repository = SemanticReleaseRepository(self.session)
        self.query_set_repository = QuerySetRepository(self.session)
        self.business_rule_repository = BusinessRuleRepository(self.session)
        self.column_repository = column_repository
        self.metric_repository = metric_repository
        self.value_repository = value_repository

    async def activate(
        self,
        *,
        build_id: str,
        domain: str,
        datasource: str,
        created_by: str,
        query_set_id: str | None = None,
    ) -> SemanticReleaseMySQL:
        build = await self._require_releasable_build(build_id, domain=domain)
        query_set = (
            await self.query_set_repository.get_version(query_set_id)
            if query_set_id is not None
            else await self.query_set_repository.get_latest_published(
                domain=domain,
                datasource=datasource,
            )
        )
        if query_set is None:
            raise SemanticReleaseError("当前作用域没有可发布的 Query Set")
        if query_set.domain != domain or query_set.datasource != datasource:
            raise SemanticReleaseError("Query Set 与发布领域或数据源不一致")
        if query_set.status != "published":
            raise SemanticReleaseError("Query Set 尚未发布")

        rule_set = await self._snapshot_published_rules(
            domain=domain,
            datasource=datasource,
            created_by=created_by,
        )
        return await self._activate_components(
            build=build,
            query_set_id=query_set.id,
            rule_set=rule_set,
            domain=domain,
            datasource=datasource,
            created_by=created_by,
            release_kind="activation",
            source_release_id=None,
        )

    async def rollback(
        self,
        release_id: str,
        *,
        created_by: str,
    ) -> SemanticReleaseMySQL:
        target = await self.release_repository.get_release(release_id)
        if target is None:
            raise LookupError("语义发布版本不存在")
        build = await self._require_releasable_build(
            target.knowledge_build_id,
            domain=target.domain,
        )
        query_set = await self.query_set_repository.get_version(target.query_set_id)
        rule_set = await self.release_repository.get_rule_set(
            target.business_rule_set_id
        )
        if query_set is None or rule_set is None:
            raise SemanticReleaseError("目标发布版本的组件快照不完整")
        return await self._activate_components(
            build=build,
            query_set_id=query_set.id,
            rule_set=rule_set,
            domain=target.domain,
            datasource=target.datasource,
            created_by=created_by,
            release_kind="rollback",
            source_release_id=target.id,
        )

    async def list_release_items(
        self,
        *,
        domain: str,
        datasource: str,
    ) -> list[dict]:
        return await list_semantic_release_items(
            self.session,
            domain=domain,
            datasource=datasource,
        )

    async def _require_releasable_build(
        self,
        build_id: str,
        *,
        domain: str,
    ) -> KnowledgeBuildMySQL:
        build = await self.session.get(KnowledgeBuildMySQL, build_id)
        if build is None:
            raise LookupError("语义构建不存在")
        if build.domain != domain:
            raise SemanticReleaseError("语义构建与发布领域不一致")
        if build.status not in {"building", "active", "superseded"}:
            raise SemanticReleaseError(f"语义构建状态不可发布：{build.status}")
        validation = await self.session.get(KnowledgeBuildValidationMySQL, build_id)
        if validation is None or validation.status != "passed":
            raise SemanticReleaseError("语义构建未通过 Golden Suite")
        return build

    async def _snapshot_published_rules(
        self,
        *,
        domain: str,
        datasource: str,
        created_by: str,
    ) -> BusinessRuleSetVersionMySQL:
        rows = await self.business_rule_repository.list_for_scope(
            domain=domain,
            datasource=datasource,
            status="published",
        )
        manifest = [
            {
                "revision_id": row.id,
                "rule_key": row.rule_key,
                "version": row.version,
                "rule_type": row.rule_type,
                "content": row.content,
                "intents": list(row.intents or []),
                "semantic_ids": list(row.semantic_ids or []),
                "priority": row.priority,
            }
            for row in sorted(rows, key=lambda item: (item.rule_key, item.version))
        ]
        content_hash = _rule_set_hash(
            manifest,
            domain=domain,
            datasource=datasource,
        )
        existing = await self.release_repository.get_rule_set_by_hash(content_hash)
        if existing is not None:
            return existing
        version = await self.release_repository.next_rule_set_version(
            domain=domain,
            datasource=datasource,
        )
        rule_set = BusinessRuleSetVersionMySQL(
            id=str(uuid.uuid4()),
            version=version,
            version_label=f"{domain}-rule-set-v{version}",
            domain=domain,
            datasource=datasource,
            content_hash=content_hash,
            manifest=manifest,
            created_by=created_by,
        )
        await self.release_repository.add_rule_set(rule_set)
        return rule_set

    async def _activate_components(
        self,
        *,
        build: KnowledgeBuildMySQL,
        query_set_id: str,
        rule_set: BusinessRuleSetVersionMySQL,
        domain: str,
        datasource: str,
        created_by: str,
        release_kind: str,
        source_release_id: str | None,
    ) -> SemanticReleaseMySQL:
        version = await self.release_repository.next_release_version(
            domain=domain,
            datasource=datasource,
        )
        release = SemanticReleaseMySQL(
            id=str(uuid.uuid4()),
            version=version,
            version_label=f"{domain}-semantic-release-v{version}",
            domain=domain,
            datasource=datasource,
            release_kind=release_kind,
            knowledge_build_id=build.id,
            query_set_id=query_set_id,
            business_rule_set_id=rule_set.id,
            source_release_id=source_release_id,
            created_by=created_by,
        )
        previous_aliases: tuple[str | None, str | None, str | None] | None = None
        try:
            await self.release_repository.add_release(release)
            previous_aliases = await _get_alias_targets(
                self.column_repository,
                self.metric_repository,
                self.value_repository,
            )
            await self.column_repository.set_alias(build.column_collection)
            await self.metric_repository.set_alias(build.metric_collection)
            await self.value_repository.set_alias(build.value_index)
            await self.meta_repository.activate_build(domain, build.id)
            await self.release_repository.set_active_release(
                domain=domain,
                datasource=datasource,
                release_id=release.id,
            )
            await self.session.commit()
            return release
        except Exception as exc:
            await self.session.rollback()
            if previous_aliases is None:
                raise SemanticReleaseError(
                    "语义发布失败，活跃版本未发生变化"
                ) from exc
            previous_column, previous_metric, previous_value = previous_aliases
            try:
                await self.column_repository.set_alias(previous_column or "")
                await self.metric_repository.set_alias(previous_metric or "")
                await self.value_repository.set_alias(previous_value or "")
            except Exception as restore_exc:
                raise SemanticReleaseRecoveryError(
                    "语义发布失败，且外部索引别名恢复失败，需要人工检查"
                ) from restore_exc
            raise SemanticReleaseError("语义发布失败，已恢复上一活跃版本") from exc


async def _get_alias_targets(
    column_repository: object,
    metric_repository: object,
    value_repository: object,
) -> tuple[str | None, str | None, str | None]:
    # Kept sequential deliberately: these clients can share one transport and this
    # happens once per release, outside the query latency path.
    return (
        await column_repository.get_alias_target(),
        await metric_repository.get_alias_target(),
        await value_repository.get_alias_target(),
    )


def _rule_set_hash(
    manifest: list[dict],
    *,
    domain: str,
    datasource: str,
) -> str:
    canonical = json.dumps(
        {"domain": domain, "datasource": datasource, "manifest": manifest},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def list_semantic_release_items(
    session: AsyncSession,
    *,
    domain: str,
    datasource: str,
) -> list[dict]:
    release_repository = SemanticReleaseRepository(session)
    query_set_repository = QuerySetRepository(session)
    active = await release_repository.get_active_release(
        domain=domain,
        datasource=datasource,
    )
    releases = await release_repository.list_releases(
        domain=domain,
        datasource=datasource,
    )
    items: list[dict] = []
    for release in releases:
        query_set = await query_set_repository.get_version(release.query_set_id)
        rule_set = await release_repository.get_rule_set(
            release.business_rule_set_id
        )
        items.append(
            {
                "id": release.id,
                "version": release.version,
                "version_label": release.version_label,
                "domain": release.domain,
                "datasource": release.datasource,
                "release_kind": release.release_kind,
                "knowledge_build_id": release.knowledge_build_id,
                "query_set_id": release.query_set_id,
                "query_set_version": query_set.version if query_set else None,
                "business_rule_set_id": release.business_rule_set_id,
                "business_rule_set_version": rule_set.version if rule_set else None,
                "source_release_id": release.source_release_id,
                "created_by": release.created_by,
                "created_at": release.created_at,
                "active": active is not None and active.id == release.id,
            }
        )
    return items
