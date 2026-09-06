import asyncio

from langchain_core.runnables import RunnableLambda

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.table_info import TableInfo


class FakeEmbeddingClient:
    async def aembed_documents(self, terms):
        return [[0.1, 0.2] for _ in terms]


class FakeColumnRepository:
    def __init__(self, columns):
        self.columns = columns

    async def search(self, embedding):
        return self.columns


class FakeMetricRepository:
    def __init__(self, metrics):
        self.metrics = metrics

    async def search(self, embedding):
        return self.metrics


class FakeValueRepository:
    async def search(self, keyword):
        return []


class FakeMetaRepository:
    def __init__(self, table, columns, metrics):
        self.table = table
        self.columns = {column.id: column for column in columns}
        self.metrics = {metric.id: metric for metric in metrics}

    async def get_active_build_id(self):
        return "fake-build"

    async def get_column_info_by_id(self, column_id, build_id=None):
        return self.columns[column_id]

    async def get_metric_info_by_id(self, metric_id, build_id=None):
        return self.metrics[metric_id]

    async def list_allowed_column_infos(self, build_id=None):
        return list(self.columns.values())

    async def get_all_relationships(self, build_id=None):
        return []

    async def get_key_columns_by_table_id(self, table_id, build_id=None):
        return [self.columns["play_session.id"]]

    async def get_table_info_by_id(self, table_id, build_id=None):
        return self.table


class FakeWarehouseRepository:
    def __init__(self):
        self.validated_sql = None

    async def get_db_info(self):
        return {"dialect": "mysql", "version": "8.0"}

    async def validate_sql(self, sql, timeout_seconds):
        self.validated_sql = sql

    async def execute_sql(self, sql, timeout_seconds):
        return [{"播放次数": 7}]


def test_fake_llm_runs_full_query_graph(monkeypatch):
    import app.agent.nodes.generate_sql as generate_sql_node

    columns = [
        ColumnInfo(
            id="play_session.id",
            name="id",
            type="bigint",
            role="primary_key",
            examples=[],
            description="播放会话主键",
            alias=[],
            table_id="play_session",
        ),
        ColumnInfo(
            id="play_session.play_start_at",
            name="play_start_at",
            type="datetime",
            role="dimension",
            examples=[],
            description="播放开始时间",
            alias=["播放时间"],
            table_id="play_session",
        ),
    ]
    metric = MetricInfo(
        id="play_count",
        name="play_count",
        description="播放会话次数",
        relevant_columns=["play_session.id", "play_session.play_start_at"],
        alias=["播放次数", "播放量"],
        formula="COUNT(*)",
        time_column="play_session.play_start_at",
    )
    table = TableInfo(
        id="play_session",
        name="play_session",
        role="fact",
        description="播放会话事实表",
    )
    warehouse = FakeWarehouseRepository()
    context = DataAgentContext(
        dw_mysql_repository=warehouse,
        meta_mysql_repository=FakeMetaRepository(table, columns, [metric]),
        column_qdrant_repository=FakeColumnRepository(columns),
        metric_qdrant_repository=FakeMetricRepository([metric]),
        value_es_repository=FakeValueRepository(),
        embedding_client=FakeEmbeddingClient(),
        feedback_learning_service=None,
    )

    async def _fake_get_llm():
        return RunnableLambda(lambda _: "SELECT COUNT(*) AS 播放次数 FROM play_session")

    monkeypatch.setattr(generate_sql_node, "get_llm", _fake_get_llm)

    async def run_graph():
        return [
            event
            async for event in graph.astream(
                input={"query": "最近7天播放次数"},
                context=context,
                stream_mode="custom",
            )
        ]

    events = asyncio.run(run_graph())
    event_types = [event["type"] for event in events]

    assert "context" in event_types
    assert "sql" in event_types
    assert "result" in event_types
    assert "answer" in event_types
    assert warehouse.validated_sql == "SELECT COUNT(*) AS 播放次数 FROM play_session LIMIT 500"
    assert next(event for event in events if event["type"] == "result")["data"] == [{"播放次数": 7}]
