"""语义层管理业务逻辑：全部读写限定在当前活跃 knowledge build。"""

from __future__ import annotations

import time

from sqlalchemy import func as sa_func
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.semantic_schema import (
    SemanticColumnUpdate,
    SemanticMetricCreate,
    SemanticMetricUpsert,
    SemanticRelationshipCreate,
    SemanticRelationshipUpsert,
    SemanticTableUpdate,
)
from app.conf.app_config import app_config
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.knowledge_build_mysql import (
    ActiveKnowledgeBuildMySQL,
    KnowledgeBuildMySQL,
)
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.relationship_info_mysql import RelationshipInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL

DEFAULT_DOMAIN = "audio"


class SemanticAdminService:
    def __init__(self, session: AsyncSession, domain: str = DEFAULT_DOMAIN):
        self.session = session
        self.domain = domain

    async def get_active_build_id(self) -> str | None:
        result = await self.session.execute(
            select(ActiveKnowledgeBuildMySQL.build_id).where(
                ActiveKnowledgeBuildMySQL.domain == self.domain
            )
        )
        return result.scalar_one_or_none()

    async def require_active_build_id(self) -> str:
        build_id = await self.get_active_build_id()
        if build_id is None:
            raise LookupError(f"业务域 {self.domain} 暂无活跃知识库构建")
        return build_id

    async def overview(self) -> dict:
        build_id = await self.get_active_build_id()
        build_created_at = None
        counts = {"tables": 0, "columns": 0, "metrics": 0, "relationships": 0}
        if build_id is not None:
            build = await self.session.get(KnowledgeBuildMySQL, build_id)
            build_created_at = build.started_at if build else None
            for key, model in (
                ("tables", TableInfoMySQL),
                ("columns", ColumnInfoMySQL),
                ("metrics", MetricInfoMySQL),
                ("relationships", RelationshipInfoMySQL),
            ):
                result = await self.session.execute(
                    select(sa_func.count())
                    .select_from(model)
                    .where(model.build_id == build_id)
                )
                counts[key] = result.scalar_one()
        return {"active_build_id": build_id, "build_created_at": build_created_at, **counts}

    # ==================== 表与字段说明 ====================

    async def list_tables(self) -> list[TableInfoMySQL]:
        build_id = await self.require_active_build_id()
        result = await self.session.execute(
            select(TableInfoMySQL)
            .where(TableInfoMySQL.build_id == build_id)
            .order_by(TableInfoMySQL.id)
        )
        return list(result.scalars().all())

    async def update_table(
        self, table_id: str, data: SemanticTableUpdate
    ) -> TableInfoMySQL | None:
        build_id = await self.require_active_build_id()
        table = await self.session.get(
            TableInfoMySQL, {"id": table_id, "build_id": build_id}
        )
        if table is None:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(table, key, value)
        await self.session.commit()
        return table

    async def list_columns(self, table_id: str) -> list[ColumnInfoMySQL]:
        build_id = await self.require_active_build_id()
        result = await self.session.execute(
            select(ColumnInfoMySQL)
            .where(
                ColumnInfoMySQL.build_id == build_id,
                ColumnInfoMySQL.table_id == table_id,
            )
            .order_by(ColumnInfoMySQL.id)
        )
        return list(result.scalars().all())

    async def update_column(
        self, column_id: str, data: SemanticColumnUpdate
    ) -> ColumnInfoMySQL | None:
        build_id = await self.require_active_build_id()
        column = await self.session.get(
            ColumnInfoMySQL, {"id": column_id, "build_id": build_id}
        )
        if column is None:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(column, key, value)
        await self.session.commit()
        return column

    # ==================== 指标口径 ====================

    async def list_metrics(self) -> list[MetricInfoMySQL]:
        build_id = await self.require_active_build_id()
        result = await self.session.execute(
            select(MetricInfoMySQL)
            .where(MetricInfoMySQL.build_id == build_id)
            .order_by(MetricInfoMySQL.id)
        )
        return list(result.scalars().all())

    async def get_metric(self, metric_id: str) -> MetricInfoMySQL | None:
        build_id = await self.require_active_build_id()
        return await self.session.get(
            MetricInfoMySQL, {"id": metric_id, "build_id": build_id}
        )

    async def create_metric(self, data: SemanticMetricCreate) -> MetricInfoMySQL:
        build_id = await self.require_active_build_id()
        existing = await self.session.get(
            MetricInfoMySQL, {"id": data.id, "build_id": build_id}
        )
        if existing is not None:
            raise ValueError(f"指标编码已存在：{data.id}")
        metric = MetricInfoMySQL(
            id=data.id,
            build_id=build_id,
            **data.model_dump(exclude={"id"}),
        )
        self.session.add(metric)
        await self.session.commit()
        return metric

    async def update_metric(
        self, metric_id: str, data: SemanticMetricUpsert
    ) -> MetricInfoMySQL | None:
        metric = await self.get_metric(metric_id)
        if metric is None:
            return None
        for key, value in data.model_dump().items():
            setattr(metric, key, value)
        await self.session.commit()
        return metric

    async def delete_metric(self, metric_id: str) -> bool:
        metric = await self.get_metric(metric_id)
        if metric is None:
            return False
        await self.session.delete(metric)
        await self.session.commit()
        return True

    # ==================== 表关联关系 ====================

    async def list_relationships(self) -> list[RelationshipInfoMySQL]:
        build_id = await self.require_active_build_id()
        result = await self.session.execute(
            select(RelationshipInfoMySQL)
            .where(RelationshipInfoMySQL.build_id == build_id)
            .order_by(RelationshipInfoMySQL.id)
        )
        return list(result.scalars().all())

    async def get_relationship(self, relationship_id: str) -> RelationshipInfoMySQL | None:
        build_id = await self.require_active_build_id()
        return await self.session.get(
            RelationshipInfoMySQL, {"id": relationship_id, "build_id": build_id}
        )

    async def create_relationship(
        self, data: SemanticRelationshipCreate
    ) -> RelationshipInfoMySQL:
        build_id = await self.require_active_build_id()
        relationship_id = data.id or (
            f"{data.source_table}.{data.source_column}"
            f"->{data.target_table}.{data.target_column}"
        )
        existing = await self.session.get(
            RelationshipInfoMySQL, {"id": relationship_id, "build_id": build_id}
        )
        if existing is not None:
            raise ValueError(f"关联关系已存在：{relationship_id}")
        relationship = RelationshipInfoMySQL(
            id=relationship_id,
            build_id=build_id,
            **data.model_dump(exclude={"id"}),
        )
        self.session.add(relationship)
        await self.session.commit()
        return relationship

    async def update_relationship(
        self, relationship_id: str, data: SemanticRelationshipUpsert
    ) -> RelationshipInfoMySQL | None:
        relationship = await self.get_relationship(relationship_id)
        if relationship is None:
            return None
        for key, value in data.model_dump().items():
            setattr(relationship, key, value)
        await self.session.commit()
        return relationship

    async def delete_relationship(self, relationship_id: str) -> bool:
        relationship = await self.get_relationship(relationship_id)
        if relationship is None:
            return False
        await self.session.delete(relationship)
        await self.session.commit()
        return True


def datasource_infos() -> list[dict]:
    """数据源展示信息：来自运行时配置，绝不含密码。"""
    return [
        {
            "key": "meta",
            "label": "元数据库（语义层）",
            "host": app_config.db_meta.host,
            "port": app_config.db_meta.port,
            "database": app_config.db_meta.database,
            "user": app_config.db_meta.user,
        },
        {
            "key": "warehouse",
            "label": "业务数据仓库",
            "host": app_config.db_dw.host,
            "port": app_config.db_dw.port,
            "database": app_config.db_dw.database,
            "user": app_config.db_dw.user,
        },
    ]


async def test_datasource(session: AsyncSession) -> tuple[bool, int | None, str | None]:
    """对给定会话执行 SELECT 1 连通性检查。"""
    try:
        started_at = time.perf_counter()
        await session.execute(text("SELECT 1"))
        return True, round((time.perf_counter() - started_at) * 1000), None
    except Exception as exc:
        return False, None, str(exc)[:300]
