from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mysql.query_feedback_mysql import (
    QueryFeedbackMySQL,
    QueryTemplateConfidenceMySQL,
)
from app.models.mysql.query_trace_mysql import QueryTraceMySQL


class QueryFeedbackRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_owned_trace(self, trace_id: str, user_id: str) -> QueryTraceMySQL | None:
        result = await self.session.execute(
            select(QueryTraceMySQL).where(
                QueryTraceMySQL.id == trace_id,
                QueryTraceMySQL.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_trace(self, trace_id: str) -> QueryFeedbackMySQL | None:
        result = await self.session.execute(
            select(QueryFeedbackMySQL).where(QueryFeedbackMySQL.trace_id == trace_id)
        )
        return result.scalar_one_or_none()

    async def add_feedback(self, feedback: QueryFeedbackMySQL) -> None:
        self.session.add(feedback)
        await self.session.flush()

    async def increment_positive_confidence(
        self,
        *,
        template_signature: str,
        domain: str,
        datasource: str,
        sql_template: str,
        parameter_types: tuple[str, ...],
        trace_id: str,
    ) -> QueryTemplateConfidenceMySQL:
        row = await self.session.get(QueryTemplateConfidenceMySQL, template_signature)
        if row is None:
            row = QueryTemplateConfidenceMySQL(
                template_signature=template_signature,
                domain=domain,
                datasource=datasource,
                sql_template=sql_template,
                parameter_types=list(parameter_types),
                positive_count=1,
                last_trace_id=trace_id,
            )
            self.session.add(row)
        else:
            row.positive_count += 1
            row.last_trace_id = trace_id
        await self.session.flush()
        return row
