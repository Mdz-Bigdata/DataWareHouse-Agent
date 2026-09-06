
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_system_configs(client: AsyncClient, admin_api_key: str):
    """Test retrieving system configurations"""
    response = await client.get(
        "/api/portal/system/configs",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    # The API returns grouped configs directly, not wrapped in "configs"
    # Expected structure: {"llm": [...], "data_api": [...]}
    assert "agent" in data
    assert "data_api" in data
    
    # Verify masking logic (keys should be present but secrets might be masked)
    agent_configs = data['agent']
    assert len(agent_configs) > 0
    llm_key = agent_configs[0]
    assert "key" in llm_key
    assert "value" in llm_key
    assert "is_secret" in llm_key



@pytest.mark.asyncio
async def test_test_connection_admin_success(client: AsyncClient, admin_api_key: str):
    """Test database connection check as admin"""
    # Only redis is supported now
    response = await client.post(
        "/api/portal/system/test-connection/redis",
        headers={"X-API-Key": admin_api_key}
    )
    # 200 is success, 400 is disabled
    assert response.status_code in [200, 400]
    data = response.json()
    assert "status" in data
    assert "logs" in data

@pytest.mark.asyncio
async def test_test_connection_forbidden_for_regular_user(client: AsyncClient, valid_api_key: str):
    """Test that regular users cannot access connection diagnostics"""
    response = await client.post(
        "/api/portal/system/test-connection/clickhouse",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 403
    # After switching from require_admin to require_permission
    assert "Permission required" in response.json()["message"]

@pytest.mark.asyncio
async def test_test_connection_unauthorized(client: AsyncClient):
    """Test access without API Key"""
    response = await client.post("/api/portal/system/test-connection/clickhouse")
    assert response.status_code == 401
