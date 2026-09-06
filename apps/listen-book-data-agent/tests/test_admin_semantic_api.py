"""语义层管理接口测试（M3a）：sqlite 内存库，共享 fixture 种子数据。"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.mysql.base import Base
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.knowledge_build_mysql import (
    ActiveKnowledgeBuildMySQL,
    KnowledgeBuildMySQL,
)
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.mysql.user_mysql import UserMySQL

BUILD_ID = str(uuid.uuid4())


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, autoflush=False, autobegin=True, autocommit=False, expire_on_commit=False
    )
    async with factory() as session:
        session.add(
            KnowledgeBuildMySQL(
                id=BUILD_ID,
                domain="audio",
                status="completed",
                config_hash="hash",
                column_collection="c",
                metric_collection="m",
                value_index="v",
            )
        )
        session.add(ActiveKnowledgeBuildMySQL(domain="audio", build_id=BUILD_ID))
        session.add(
            TableInfoMySQL(
                id="audio_album",
                build_id=BUILD_ID,
                name="audio_album",
                role="fact",
                description="专辑表",
                domain="audio",
                alias=["专辑"],
            )
        )
        session.add(
            ColumnInfoMySQL(
                id="audio_album.id",
                build_id=BUILD_ID,
                table_id="audio_album",
                name="id",
                type="bigint",
                role="primary_key",
                examples=[1],
                description="主键",
                alias=[],
                nullable=False,
                sensitive=False,
                sync=False,
                enum_values=[],
            )
        )
        session.add(
            MetricInfoMySQL(
                id="album_count",
                build_id=BUILD_ID,
                name="album_count",
                description="专辑总数",
                relevant_columns=["audio_album.id"],
                alias=["专辑数"],
                formula="COUNT(DISTINCT audio_album.id)",
                filters=[],
                time_column=None,
                unit="count",
                currency_column=None,
                dimensions=[],
                snapshot=True,
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
def semantic_client(session_factory):
    from app.agent.dependencies import get_dw_session, get_meta_session
    from app.api.deps import get_current_user
    from main import app

    state = {"role": "admin"}

    async def _session():
        async with session_factory() as session:
            yield session

    async def _current():
        return UserMySQL(
            id="u1",
            username=state["role"],
            password_hash="",
            role=state["role"],
            must_change_password=False,
        )

    app.dependency_overrides[get_meta_session] = _session
    app.dependency_overrides[get_dw_session] = _session
    app.dependency_overrides[get_current_user] = _current
    yield TestClient(app), state, session_factory
    app.dependency_overrides.clear()


def test_semantic_requires_admin(semantic_client):
    client, state, _ = semantic_client
    state["role"] = "user"
    assert client.get("/api/admin/semantic/overview").status_code == 403
    assert (
        client.post("/api/admin/semantic/datasources/test", json={"target": "meta"}).status_code
        == 403
    )
    assert client.get("/api/admin/semantic/releases").status_code == 403


def test_overview_returns_active_build_counts(semantic_client):
    client, _, _ = semantic_client
    response = client.get("/api/admin/semantic/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["active_build_id"] == BUILD_ID
    assert data["tables"] == 1
    assert data["columns"] == 1
    assert data["metrics"] == 1
    assert data["relationships"] == 0
    keys = {ds["key"] for ds in data["datasources"]}
    assert keys == {"meta", "warehouse"}
    assert "password" not in response.text


def test_release_list_is_empty_before_first_atomic_release(semantic_client):
    client, _, _ = semantic_client
    response = client.get("/api/admin/semantic/releases")
    assert response.status_code == 200
    assert response.json() == []


def test_datasource_test_ok(semantic_client):
    client, _, _ = semantic_client
    for target in ("meta", "warehouse"):
        response = client.post(
            "/api/admin/semantic/datasources/test", json={"target": target}
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_list_tables(semantic_client):
    client, _, _ = semantic_client
    response = client.get("/api/admin/semantic/tables")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "audio_album",
            "name": "audio_album",
            "role": "fact",
            "description": "专辑表",
            "alias": ["专辑"],
            "domain": "audio",
        }
    ]


def test_update_table_partial(semantic_client):
    client, _, _ = semantic_client
    response = client.put(
        "/api/admin/semantic/tables/audio_album",
        json={"description": "有声专辑事实表", "alias": ["专辑", "有声书"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "有声专辑事实表"
    assert data["alias"] == ["专辑", "有声书"]
    assert data["name"] == "audio_album"  # 未传字段保持不变


def test_update_table_missing_returns_404(semantic_client):
    client, _, _ = semantic_client
    assert (
        client.put("/api/admin/semantic/tables/nope", json={"name": "x"}).status_code
        == 404
    )


def test_list_columns_of_table(semantic_client):
    client, _, _ = semantic_client
    response = client.get("/api/admin/semantic/tables/audio_album/columns")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "audio_album.id"
    assert data[0]["sensitive"] is False


def test_update_column_flags(semantic_client):
    client, _, _ = semantic_client
    response = client.put(
        "/api/admin/semantic/columns/audio_album.id",
        json={"description": "专辑主键", "alias": ["专辑ID"], "sensitive": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "专辑主键"
    assert data["sensitive"] is True
    assert data["type"] == "bigint"  # 未传字段保持不变


METRIC_BODY = {
    "name": "播放次数",
    "description": "播放会话总数",
    "alias": ["播放量"],
    "formula": "COUNT(*)",
    "relevant_columns": ["play_session.id"],
    "filters": [],
    "time_column": "play_session.play_start_at",
    "unit": "count",
    "dimensions": [],
    "snapshot": False,
}


def test_metric_crud_flow(semantic_client):
    client, _, _ = semantic_client

    created = client.post(
        "/api/admin/semantic/metrics", json={"id": "play_count", **METRIC_BODY}
    )
    assert created.status_code == 201
    assert created.json()["formula"] == "COUNT(*)"

    # 编码重复 → 409
    duplicate = client.post(
        "/api/admin/semantic/metrics", json={"id": "play_count", **METRIC_BODY}
    )
    assert duplicate.status_code == 409

    listing = client.get("/api/admin/semantic/metrics")
    ids = [item["id"] for item in listing.json()]
    assert ids == ["album_count", "play_count"]

    updated = client.put(
        "/api/admin/semantic/metrics/play_count",
        json={**METRIC_BODY, "description": "修正后的口径"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "修正后的口径"

    deleted = client.delete("/api/admin/semantic/metrics/play_count")
    assert deleted.status_code == 200
    remaining = [item["id"] for item in client.get("/api/admin/semantic/metrics").json()]
    assert remaining == ["album_count"]


def test_metric_missing_returns_404(semantic_client):
    client, _, _ = semantic_client
    assert client.put("/api/admin/semantic/metrics/nope", json=METRIC_BODY).status_code == 404
    assert client.delete("/api/admin/semantic/metrics/nope").status_code == 404


REL_BODY = {
    "source_table": "play_session",
    "source_column": "album_id",
    "target_table": "audio_album",
    "target_column": "id",
    "relationship_type": "many_to_one",
    "condition": None,
    "physical": True,
}


def test_relationship_crud_flow(semantic_client):
    client, _, _ = semantic_client

    # id 留空自动生成
    created = client.post("/api/admin/semantic/relationships", json=REL_BODY)
    assert created.status_code == 201
    auto_id = created.json()["id"]
    assert auto_id == "play_session.album_id->audio_album.id"

    duplicate = client.post(
        "/api/admin/semantic/relationships", json={"id": auto_id, **REL_BODY}
    )
    assert duplicate.status_code == 409

    listing = client.get("/api/admin/semantic/relationships")
    assert [item["id"] for item in listing.json()] == [auto_id]

    updated = client.put(
        f"/api/admin/semantic/relationships/{auto_id}",
        json={**REL_BODY, "condition": "play_session.deleted = 0"},
    )
    assert updated.status_code == 200
    assert updated.json()["condition"] == "play_session.deleted = 0"

    assert client.delete(f"/api/admin/semantic/relationships/{auto_id}").status_code == 200
    assert client.get("/api/admin/semantic/relationships").json() == []


def test_relationship_missing_returns_404(semantic_client):
    client, _, _ = semantic_client
    assert (
        client.put("/api/admin/semantic/relationships/nope", json=REL_BODY).status_code
        == 404
    )
    assert client.delete("/api/admin/semantic/relationships/nope").status_code == 404
