"""召回测试与重建接口测试（M3c）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.models.mysql.user_mysql import UserMySQL


class FakeEmbeddingClient:
    async def aembed_documents(self, texts):
        return [[0.1] * 8 for _ in texts]


class FakeColumnQdrantRepository:
    async def search(self, embedding, score_threshold=0.6, limit=10):
        return [
            ColumnInfo(
                id="audio_album.id",
                name="id",
                type="bigint",
                role="primary_key",
                examples=[],
                description="专辑主键",
                alias=[],
                table_id="audio_album",
            ),
            ColumnInfo(
                id="user_account.mobile",
                name="mobile",
                type="varchar",
                role="dimension",
                examples=[],
                description="手机号",
                alias=[],
                table_id="user_account",
                sensitive=True,  # 敏感字段应被过滤
            ),
        ]


class FakeMetricQdrantRepository:
    async def search(self, embedding, score_threshold=0.6, limit=10):
        return [
            MetricInfo(
                id="album_count",
                name="album_count",
                description="专辑总数",
                relevant_columns=["audio_album.id"],
                alias=["专辑数"],
                formula="COUNT(DISTINCT audio_album.id)",
            )
        ]


@pytest.fixture
def recall_client():
    from main import app
    from app.agent.dependencies import (
        get_column_qdrant_repository,
        get_embedding_client,
        get_meta_mysql_repository,
        get_metric_qdrant_repository,
    )
    from app.api.deps import get_current_user

    async def _user():
        return UserMySQL(
            id="u1", username="admin", password_hash="", role="admin",
            must_change_password=False,
        )

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    app.dependency_overrides[get_column_qdrant_repository] = (
        lambda: FakeColumnQdrantRepository()
    )
    app.dependency_overrides[get_metric_qdrant_repository] = (
        lambda: FakeMetricQdrantRepository()
    )
    app.dependency_overrides[get_meta_mysql_repository] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_recall_test_returns_tables_columns_metrics(recall_client):
    response = recall_client.post(
        "/api/admin/semantic/recall-test", json={"question": "平台一共有多少个有声专辑"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "平台一共有多少个有声专辑" in data["keywords"]
    assert data["tables"] == ["audio_album"]
    column_ids = [column["id"] for column in data["columns"]]
    assert "audio_album.id" in column_ids
    assert "user_account.mobile" not in column_ids  # 敏感字段被过滤
    assert data["metrics"][0]["id"] == "album_count"
    assert data["warnings"] == []


def test_recall_test_validates_question(recall_client):
    assert recall_client.post("/api/admin/semantic/recall-test", json={"question": ""}).status_code == 422


def test_rebuild_status_and_conflict(recall_client, monkeypatch):
    # 初始状态
    response = recall_client.get("/api/admin/semantic/rebuild/status")
    assert response.status_code == 200
    assert response.json()["status"] in {"idle", "completed", "failed", "running"}

    # 锁被占用时启动 → 409
    import asyncio
    from app.services import knowledge_rebuild_service

    async def _locked():
        async with knowledge_rebuild_service._lock:
            return await knowledge_rebuild_service.start_rebuild()

    started = asyncio.run(_locked())
    assert started is False


def test_collect_value_infos_dedup_and_cap():
    from app.services.knowledge_rebuild_service import _collect_value_infos

    columns = [
        ColumnInfo(
            id="dim_channel.name",
            name="name",
            type="varchar",
            role="dimension",
            examples=["新闻", "音乐"],
            description="渠道",
            alias=[],
            table_id="dim_channel",
            sync=True,
            enum_values=["新闻", "财经"],  # 与 examples 有重复
        ),
        ColumnInfo(
            id="audio_album.id",
            name="id",
            type="bigint",
            role="primary_key",
            examples=[1],
            description="主键",
            alias=[],
            table_id="audio_album",
            sync=False,  # 未开启同步，不应产出
        ),
    ]
    values = _collect_value_infos(columns, "rebuild1")
    texts = [info.value for info in values]
    assert texts == ["新闻", "财经", "音乐"]  # enum_values 在前，去重后补 examples
    assert all(info.build_id == "rebuild1" for info in values)
    assert all(info.column_id == "dim_channel.name" for info in values)
