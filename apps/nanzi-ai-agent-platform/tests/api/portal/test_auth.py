import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_me_with_valid_key(client: AsyncClient, valid_api_key: str):
    """Test getting current user info with valid API key"""
    response = await client.get(
        "/api/portal/auth/me",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["status"] == "success"
    data = res_json["data"]
    assert "user_id" in data
    assert "user_name" in data
    assert "role" in data
    # assert "permissions" in data  # Removed
    assert data["status"] == "active"

@pytest.mark.asyncio
async def test_get_me_with_admin_key(client: AsyncClient, admin_api_key: str):
    """Test getting admin user info"""
    response = await client.get(
        "/api/portal/auth/me",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["status"] == "success"
    data = res_json["data"]
    assert data["role"] == "admin"
    assert data["user_name"] == "test_admin"

    @pytest.mark.asyncio
    async def test_get_me_without_key(client: AsyncClient):
        """Test that request without API key fails"""
        response = await client.get("/api/portal/auth/me")
        assert response.status_code == 401
        assert "Missing API Key" in response.json()["message"]
    @pytest.mark.asyncio
    async def test_get_me_with_invalid_key(client: AsyncClient):
        """Test that invalid API key fails"""
        response = await client.get(
            "/api/portal/auth/me",
            headers={"X-API-Key": "invalid_key_12345"}
        )
        assert response.status_code == 401
        assert "Invalid API Key" in response.json()["message"]
@pytest.mark.asyncio
async def test_admin_login_success(client: AsyncClient, admin_api_key: str):
    """Test admin login with correct credentials"""
    response = await client.post(
        "/api/portal/auth/login",
        json={
            "user_name": "test_admin",
            "api_key": admin_api_key
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "data" in data
    assert data["data"]["user_name"] == "test_admin"
    assert data["data"]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_admin_login_with_wrong_key(client: AsyncClient):
        """Test admin login with wrong API key"""
        response = await client.post(
            "/api/portal/auth/login",
            json={
                "user_name": "test_admin",
                "api_key": "wrong_key"
            }
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["message"]
    @pytest.mark.asyncio
    async def test_admin_login_with_user_key(client: AsyncClient, valid_api_key: str):
        """Test that regular user cannot login as admin"""
        response = await client.post(
            "/api/portal/auth/login",
            json={
                "user_name": "test_user",
                "api_key": valid_api_key
            }
        )
        assert response.status_code == 401
        assert "not an admin" in response.json()["message"]
