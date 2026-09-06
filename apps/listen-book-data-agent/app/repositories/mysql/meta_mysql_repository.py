from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.relationship_info import RelationshipInfo
from app.entities.table_info import TableInfo
from app.mappers.column_info_mapper import ColumnInfoMapper
from app.mappers.column_metric_mapper import ColumnMetricMapper
from app.mappers.metric_info_mapper import MetricInfoMapper
from app.mappers.relationship_info_mapper import RelationshipInfoMapper
from app.mappers.table_info_mapper import TableInfoMapper
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.knowledge_build_mysql import (
    ActiveKnowledgeBuildMySQL,
    KnowledgeBuildMySQL,
    KnowledgeBuildValidationMySQL,
)
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.relationship_info_mysql import RelationshipInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL


class MetaMySQlRepository:
    """Persist and query versioned semantic metadata."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_table_infos(self, table_infos: list[TableInfo]) -> None:
        self.session.add_all([TableInfoMapper.to_model(item) for item in table_infos])

    async def save_column_infos(self, column_infos: list[ColumnInfo]) -> None:
        self.session.add_all([ColumnInfoMapper.to_model(item) for item in column_infos])

    async def save_metric_infos(self, metric_infos: list[MetricInfo]) -> None:
        self.session.add_all([MetricInfoMapper.to_model(item) for item in metric_infos])

    async def save_column_metrics(self, column_metrics: list[ColumnMetric]) -> None:
        self.session.add_all(
            [ColumnMetricMapper.to_model(item) for item in column_metrics]
        )

    async def save_relationship_infos(
        self, relationship_infos: list[RelationshipInfo]
    ) -> None:
        self.session.add_all(
            [RelationshipInfoMapper.to_model(item) for item in relationship_infos]
        )

    async def create_knowledge_build(
        self,
        *,
        build_id: str,
        domain: str,
        config_hash: str,
        column_collection: str,
        metric_collection: str,
        value_index: str,
        table_count: int,
        column_count: int,
        metric_count: int,
        relationship_count: int,
    ) -> None:
        self.session.add(
            KnowledgeBuildMySQL(
                id=build_id,
                domain=domain,
                status="building",
                config_hash=config_hash,
                column_collection=column_collection,
                metric_collection=metric_collection,
                value_index=value_index,
                table_count=table_count,
                column_count=column_count,
                metric_count=metric_count,
                relationship_count=relationship_count,
            )
        )

    async def find_active_build_by_hash(
        self, domain: str, config_hash: str
    ) -> KnowledgeBuildMySQL | None:
        stmt = (
            select(KnowledgeBuildMySQL)
            .join(
                ActiveKnowledgeBuildMySQL,
                ActiveKnowledgeBuildMySQL.build_id == KnowledgeBuildMySQL.id,
            )
            .where(
                ActiveKnowledgeBuildMySQL.domain == domain,
                KnowledgeBuildMySQL.config_hash == config_hash,
                KnowledgeBuildMySQL.status == "active",
            )
        )
        return await self.session.scalar(stmt)

    async def mark_build_failed(self, build_id: str, error_message: str) -> None:
        build = await self.session.get(KnowledgeBuildMySQL, build_id)
        if build:
            build.status = "failed"
            build.error_message = error_message[:8000]
            build.completed_at = datetime.now()

    async def save_build_validation(self, report: dict) -> None:
        """Persist the immutable pre-activation Golden Suite report."""

        self.session.add(
            KnowledgeBuildValidationMySQL(
                build_id=str(report["candidate_build_id"]),
                suite_version=str(report["suite_version"]),
                status="passed" if report["passed"] else "failed",
                semantic_accuracy=float(report["candidate"]["semantic_accuracy"]),
                baseline_semantic_accuracy=(
                    float(report["baseline"]["semantic_accuracy"])
                    if report.get("baseline") is not None
                    else None
                ),
                safety_accuracy=float(report["safety_accuracy"]),
                p95_latency_ms=float(report["candidate"]["p95_latency_ms"]),
                baseline_p95_latency_ms=(
                    float(report["baseline"]["p95_latency_ms"])
                    if report.get("baseline") is not None
                    else None
                ),
                report=report,
            )
        )

    async def activate_build(self, domain: str, build_id: str) -> None:
        current_id = await self.get_active_build_id(domain)
        if current_id and current_id != build_id:
            current = await self.session.get(KnowledgeBuildMySQL, current_id)
            if current:
                current.status = "superseded"

        active = await self.session.get(ActiveKnowledgeBuildMySQL, domain)
        if active is None:
            self.session.add(
                ActiveKnowledgeBuildMySQL(domain=domain, build_id=build_id)
            )
        else:
            active.build_id = build_id
            active.updated_at = datetime.now()

        build = await self.session.get(KnowledgeBuildMySQL, build_id)
        if build is None:
            raise LookupError(f"knowledge build not found: {build_id}")
        build.status = "active"
        build.error_message = None
        build.completed_at = datetime.now()

    async def get_active_build_id(self, domain: str = "audio") -> str | None:
        return await self.session.scalar(
            select(ActiveKnowledgeBuildMySQL.build_id).where(
                ActiveKnowledgeBuildMySQL.domain == domain
            )
        )

    async def list_table_infos(
        self, build_id: str | None = None
    ) -> list[TableInfo]:
        resolved = await self._resolve_build_id(build_id)
        result = await self.session.scalars(
            select(TableInfoMySQL).where(TableInfoMySQL.build_id == resolved)
        )
        return [TableInfoMapper.to_entity(model) for model in result]

    async def _resolve_build_id(
        self, build_id: str | None, domain: str = "audio"
    ) -> str:
        resolved = build_id or await self.get_active_build_id(domain)
        if not resolved:
            raise LookupError(f"no active knowledge build for domain {domain!r}")
        return resolved

    async def get_column_info_by_id(
        self, column_id: str, build_id: str | None = None
    ) -> ColumnInfo:
        resolved = await self._resolve_build_id(build_id)
        model = await self.session.get(
            ColumnInfoMySQL,
            {"id": column_id, "build_id": resolved},
        )
        if model is None:
            raise LookupError(f"column metadata not found: {column_id}")
        return ColumnInfoMapper.to_entity(model)

    async def get_key_columns_by_table_id(
        self, table_id: str, build_id: str | None = None
    ) -> list[ColumnInfo]:
        resolved = await self._resolve_build_id(build_id)
        stmt = select(ColumnInfoMySQL).where(
            ColumnInfoMySQL.build_id == resolved,
            ColumnInfoMySQL.table_id == table_id,
            ColumnInfoMySQL.role.in_(["primary_key", "foreign_key"]),
        )
        result = await self.session.scalars(stmt)
        return [ColumnInfoMapper.to_entity(model) for model in result]

    async def list_allowed_column_infos(
        self, build_id: str | None = None
    ) -> list[ColumnInfo]:
        """Return only columns permitted to participate in generated SQL."""

        resolved = await self._resolve_build_id(build_id)
        stmt = select(ColumnInfoMySQL).where(
            ColumnInfoMySQL.build_id == resolved,
            ColumnInfoMySQL.sensitive.is_(False),
        )
        result = await self.session.scalars(stmt)
        return [ColumnInfoMapper.to_entity(model) for model in result]

    async def list_metric_infos(
        self, build_id: str | None = None
    ) -> list[MetricInfo]:
        resolved = await self._resolve_build_id(build_id)
        result = await self.session.scalars(
            select(MetricInfoMySQL).where(MetricInfoMySQL.build_id == resolved)
        )
        return [MetricInfoMapper.to_entity(model) for model in result]

    async def get_table_info_by_id(
        self, table_id: str, build_id: str | None = None
    ) -> TableInfo:
        resolved = await self._resolve_build_id(build_id)
        model = await self.session.get(
            TableInfoMySQL,
            {"id": table_id, "build_id": resolved},
        )
        if model is None:
            raise LookupError(f"table metadata not found: {table_id}")
        return TableInfoMapper.to_entity(model)

    async def get_metric_info_by_id(
        self, metric_id: str, build_id: str | None = None
    ) -> MetricInfo:
        resolved = await self._resolve_build_id(build_id)
        model = await self.session.get(
            MetricInfoMySQL,
            {"id": metric_id, "build_id": resolved},
        )
        if model is None:
            raise LookupError(f"metric metadata not found: {metric_id}")
        return MetricInfoMapper.to_entity(model)

    async def get_relationships_by_table_ids(
        self,
        table_ids: list[str],
        build_id: str | None = None,
    ) -> list[RelationshipInfo]:
        resolved = await self._resolve_build_id(build_id)
        stmt = select(RelationshipInfoMySQL).where(
            RelationshipInfoMySQL.build_id == resolved,
            RelationshipInfoMySQL.source_table.in_(table_ids)
            | RelationshipInfoMySQL.target_table.in_(table_ids),
        )
        result = await self.session.scalars(stmt)
        return [RelationshipInfoMapper.to_entity(model) for model in result]

    async def get_all_relationships(
        self, build_id: str | None = None
    ) -> list[RelationshipInfo]:
        resolved = await self._resolve_build_id(build_id)
        result = await self.session.scalars(
            select(RelationshipInfoMySQL).where(
                RelationshipInfoMySQL.build_id == resolved
            )
        )
        return [RelationshipInfoMapper.to_entity(model) for model in result]
