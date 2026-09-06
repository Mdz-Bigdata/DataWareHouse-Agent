from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.dependencies import get_query_service, get_query_trace_repository
from app.api.deps import get_current_user
from app.entities.column_info import ColumnInfo
from app.entities.table_info import TableInfo
from app.models.mysql.user_mysql import UserMySQL
from app.services.access_policy import AccessPolicyContextV1, internal_access_policy
from app.services.deep_analysis_service import ANALYSIS_ROW_LIMIT, DeepAnalysisService


class FakeDWRepository:
    def __init__(self):
        self.executed_sql = ""

    async def validate_sql(self, sql: str, timeout_seconds: int):
        assert timeout_seconds <= 10
        self.executed_sql = sql
        return None

    async def execute_sql(self, sql: str, timeout_seconds: int):
        assert timeout_seconds <= 10
        self.executed_sql = sql
        return [
            {"channel": "自然", "play_count": 20},
            {"channel": "广告", "play_count": 5},
        ]


class FakeMetaRepository:
    async def get_active_build_id(self, domain: str):
        assert domain == "audio"
        return "build-current"

    async def list_table_infos(self, build_id: str):
        return [TableInfo(id="channel_stat", name="channel_stat", role="fact", description="")]

    async def list_allowed_column_infos(self, build_id: str):
        return [
            ColumnInfo(
                id="channel_stat.channel",
                name="channel",
                type="varchar",
                role="dimension",
                examples=[],
                description="",
                alias=[],
                table_id="channel_stat",
            ),
            ColumnInfo(
                id="channel_stat.play_count",
                name="play_count",
                type="bigint",
                role="measure",
                examples=[],
                description="",
                alias=[],
                table_id="channel_stat",
            ),
        ]

    async def get_all_relationships(self, build_id: str):
        return []


@dataclass
class FakeTraceRepository:
    owner_id: str = "user-1"
    created: list[dict] = field(default_factory=list)
    phases: list[dict] = field(default_factory=list)
    finished: list[dict] = field(default_factory=list)

    async def get_for_user(self, trace_id: str, user_id: str):
        if trace_id != "trace-source" or user_id != self.owner_id:
            return None
        return SimpleNamespace(
            id="trace-source",
            user_id=self.owner_id,
            query_text="按渠道统计播放量",
            standalone_question="按渠道统计播放量",
            status="completed",
            sql=(
                "SELECT channel, play_count FROM channel_stat "
                "ORDER BY play_count DESC LIMIT 500"
            ),
            conversation_id="conversation-1",
        )

    async def create_trace(self, trace_id, query_text, user_id, **kwargs):
        self.created.append(
            {"trace_id": trace_id, "query_text": query_text, "user_id": user_id, **kwargs}
        )

    async def record_phase(self, **kwargs):
        self.phases.append(kwargs)

    async def finish_trace(self, **kwargs):
        self.finished.append(kwargs)


@pytest.mark.asyncio
async def test_deep_analysis_reauthorizes_caps_rows_and_persists_no_result_rows():
    traces = FakeTraceRepository()
    warehouse = FakeDWRepository()
    result = await DeepAnalysisService(
        warehouse,
        FakeMetaRepository(),
        traces,
    ).analyze(
        source_trace_id="trace-source",
        user_id="user-1",
        access_policy=internal_access_policy(domain="audio", datasource="audio_full"),
    )

    assert result["source_trace_id"] == "trace-source"
    assert result["rerun_row_count"] == 2
    assert result["row_limit"] == ANALYSIS_ROW_LIMIT
    assert f"LIMIT {ANALYSIS_ROW_LIMIT}" in warehouse.executed_sql
    assert result["facts"] and result["evidence"]
    assert result["inferences"][0]["fact_ids"] == ["fact-numeric-1"]
    assert traces.created[0]["parent_trace_id"] == "trace-source"
    assert traces.finished[-1]["status"] == "completed"
    assert "rows" not in traces.finished[-1]
    assert "evidence" not in traces.finished[-1]


@pytest.mark.asyncio
async def test_deep_analysis_is_owner_scoped():
    with pytest.raises(LookupError, match="不存在"):
        await DeepAnalysisService(
            FakeDWRepository(),
            FakeMetaRepository(),
            FakeTraceRepository(),
        ).analyze(
            source_trace_id="trace-source",
            user_id="other-user",
            access_policy=internal_access_policy(domain="audio", datasource="audio_full"),
        )


@pytest.mark.asyncio
async def test_deep_analysis_rejects_sql_when_current_acl_revokes_a_column():
    traces = FakeTraceRepository()
    warehouse = FakeDWRepository()
    policy = AccessPolicyContextV1(
        user_id="user-1",
        role="user",
        domain="audio",
        datasource="audio_full",
        table_acl={"channel_stat": ("channel",)},
        function_whitelist=("COUNT",),
        policy_version="policy-revoked",
        policy_hash="hash-revoked",
    )

    with pytest.raises(ValueError, match="字段未授权"):
        await DeepAnalysisService(
            warehouse,
            FakeMetaRepository(),
            traces,
        ).analyze(
            source_trace_id="trace-source",
            user_id="user-1",
            access_policy=policy,
        )

    assert warehouse.executed_sql == ""
    assert traces.finished[-1]["status"] == "failed"
    assert traces.finished[-1]["query_plan_summary"]["source_trace_id"] == "trace-source"


def test_deep_analysis_endpoint_returns_structured_report_and_enforces_owner():
    from main import app

    traces = FakeTraceRepository()
    service = SimpleNamespace(
        dw_mysql_repository=FakeDWRepository(),
        meta_mysql_repository=FakeMetaRepository(),
    )
    state = {"user_id": "user-1"}

    async def _query_service():
        return service

    async def _trace_repository():
        return traces

    async def _current_user():
        return UserMySQL(
            id=state["user_id"],
            username=state["user_id"],
            password_hash="",
            role="admin",
            must_change_password=False,
        )

    app.dependency_overrides[get_query_service] = _query_service
    app.dependency_overrides[get_query_trace_repository] = _trace_repository
    app.dependency_overrides[get_current_user] = _current_user
    try:
        client = TestClient(app)
        response = client.post("/api/traces/trace-source/analysis")
        assert response.status_code == 200
        payload = response.json()
        assert payload["source_trace_id"] == "trace-source"
        assert payload["facts"] and payload["evidence"]
        assert payload["disclaimer"].endswith("不包含未来预测。")

        state["user_id"] = "other-user"
        denied = client.post("/api/traces/trace-source/analysis")
        assert denied.status_code == 404
    finally:
        app.dependency_overrides.clear()
