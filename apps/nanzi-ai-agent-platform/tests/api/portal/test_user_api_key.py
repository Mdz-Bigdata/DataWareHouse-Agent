"""
Test user API key retrieval functionality
"""
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_regular_user_can_get_own_api_key(client: AsyncClient, valid_api_key: str):
    """Test that a regular user can retrieve their own API key"""
    # First, get user info to get user_id
    response = await client.get(
        "/api/portal/auth/me",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    user_data = response.json()["data"]
    user_id = int(user_data["user_id"])  # Convert to int for comparison
    
    # Now try to get own API key
    response = await client.get(
        f"/api/portal/management/api-key/{user_id}",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    assert "api_key" in data
    assert data["user_id"] == user_id

@pytest.mark.asyncio
async def test_regular_user_cannot_get_other_user_api_key(client: AsyncClient, valid_api_key: str):
    """Test that a regular user cannot retrieve another user's API key"""
    # 先拿自己的 ID
    me_resp = await client.get("/api/portal/auth/me", headers={"X-API-Key": valid_api_key})
    my_id = int(me_resp.json()["data"]["user_id"])
    other_id = my_id + 1 if my_id == 1 else 1 # 确保不是自己
    
    response = await client.get(
        f"/api/portal/management/api-key/{other_id}",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 403
    data = response.json()
    msg = (data.get("message") or data.get("detail") or "").lower()
    assert "view_key" in msg or "only view your own" in msg

@pytest.mark.asyncio
async def test_admin_can_get_any_user_api_key(client: AsyncClient, admin_api_key: str):
    """Test that admin can retrieve any user's API key"""
    # 动态获取 test_user 的 ID
    search_resp = await client.get(
        "/api/portal/management/users?search=test_user",
        headers={"X-API-Key": admin_api_key}
    )
    assert search_resp.status_code == 200
    # 修正：management/users 接口直接返回 {items: [...]}，没有外层 data 包装
    resp_json = search_resp.json()
    test_user = resp_json["items"][0]
    user_id = test_user["id"]
    
    # Get test_user's API key
    response = await client.get(
        f"/api/portal/management/api-key/{user_id}",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    assert "api_key" in data
    assert int(data["user_id"]) == user_id

@pytest.mark.asyncio
async def test_unauthenticated_cannot_get_api_key(client: AsyncClient):
    """Test that unauthenticated request fails"""
    response = await client.get("/api/portal/management/api-key/1")
    assert response.status_code == 401