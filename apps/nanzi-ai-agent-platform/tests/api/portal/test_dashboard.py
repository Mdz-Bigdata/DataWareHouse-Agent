"""
Tests for Dashboard API endpoints
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_stats_default(client: AsyncClient, admin_api_key: str):
    """Test admin stats with default period (today)"""
    response = await client.get(
        "/api/portal/dashboard/admin-stats",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Check required fields
    assert "total_users" in data
    assert "active_users" in data
    assert "api_calls" in data
    assert "avg_response_time" in data
    assert "success_rate" in data
    assert "error_rate" in data
    
    # Check api_calls structure
    assert data["api_calls"]["period"] == "today"
    assert "total" in data["api_calls"]
    assert "success" in data["api_calls"]
    assert "errors" in data["api_calls"]


@pytest.mark.asyncio
async def test_admin_stats_accessible_for_user(client: AsyncClient, valid_api_key: str):
    """Test that regular users can access stats (but see only their own data)"""
    response = await client.get(
        "/api/portal/dashboard/admin-stats",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Regular users should see api_calls but not total_users/active_users
    assert "api_calls" in data
    assert "avg_response_time" in data
    assert "success_rate" in data
    assert "error_rate" in data
    
    # Admin-only fields should NOT be present for regular users
    assert "total_users" not in data
    assert "active_users" not in data


@pytest.mark.asyncio
async def test_user_stats_default(client: AsyncClient, valid_api_key: str):
    """Test user stats with default period"""
    response = await client.get(
        "/api/portal/dashboard/user-stats",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Check required fields
    assert "api_key_status" in data
    assert "api_calls" in data
    assert "avg_response_time" in data
    assert "success_rate" in data
    assert "last_call_time" in data
    
    # Check API key status
    assert data["api_key_status"] in ["active", "inactive"]
    
    # Check api_calls structure
    assert data["api_calls"]["period"] == "today"


@pytest.mark.asyncio
async def test_api_trends_default(client: AsyncClient, admin_api_key: str):
    """Test API trends with default days (7)"""
    response = await client.get(
        "/api/portal/dashboard/api-trends",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Should return an array
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_recent_activities_admin(client: AsyncClient, admin_api_key: str):
    """Test recent activities for admin"""
    response = await client.get(
        "/api/portal/dashboard/recent-activities",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Admins should see all sections
    assert "recent_users" in data
    assert "recent_calls" in data
    assert "recent_errors" in data



