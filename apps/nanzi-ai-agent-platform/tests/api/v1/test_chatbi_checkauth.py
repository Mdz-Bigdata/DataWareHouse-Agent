"""
校验 V1 ChatBI 公开路由：POST /api/v1/chatbi/sql/checkauth 已挂载且可调用（不依赖外部 SQL API）。
"""
import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_chatbi_sql_checkauth_route_post_ok(client: AsyncClient):
    """路由存在：POST 命中 public_router，不经 require_api_key；核心链用 patch 固定成功返回。"""
    async def fake_execute_sql_query_core(*args, **kwargs):
        assert kwargs.get("auth_check_only") is True
        assert kwargs.get("dry_run") is False
        return json.dumps({"allowed": True})

    with patch(
        "app.api.v1.endpoints.chatbi.execute_sql_query_core",
        side_effect=fake_execute_sql_query_core,
    ):
        response = await client.post(
            "/api/v1/chatbi/sql/checkauth",
            json={
                "username": "test_user",
                "sql": "SELECT 1",
                "data_source": "default_clickhouse",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("code") == 200
    assert body.get("message") == "success"
    assert body.get("data") == {"allowed": True}


@pytest.mark.asyncio
async def test_chatbi_sql_checkauth_unknown_user_404(client: AsyncClient):
    """Body 中 username 在库中不存在时应 404，且不应落到 SPA 的 API Not Found。"""
    response = await client.post(
        "/api/v1/chatbi/sql/checkauth",
        json={
            "username": "__no_such_user_chatbi_checkauth__",
            "sql": "SELECT 1",
            "data_source": "default_clickhouse",
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert "用户不存在" in str(body.get("message", "")) or "已禁用" in str(body.get("message", ""))


@pytest.mark.asyncio
async def test_chatbi_sql_execute_still_requires_api_key(client: AsyncClient):
    """对照：同前缀下的 execute 仍在 secured 树上，无 Key 应 401。"""
    response = await client.post(
        "/api/v1/chatbi/sql/execute",
        json={
            "sql": "SELECT 1",
            "data_source": "default_clickhouse",
        },
    )
    assert response.status_code == 401
