import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.auth_service import AuthService
from app.models.user import User
from sqlalchemy import select

@pytest.mark.asyncio
async def test_user_profile_access(db_session):
    # 1. Setup Users with unique names
    import uuid
    uid_suffix = str(uuid.uuid4())[:8]
    
    admin_name = f"test_v1_admin_{uid_suffix}"
    user_name = f"test_v1_user_{uid_suffix}"
    target_name = f"test_v1_target_{uid_suffix}"
    
    admin_key = await AuthService.generate_api_key(admin_name, role="admin", db=db_session)
    user_key = await AuthService.generate_api_key(user_name, role="user", db=db_session)
    target_user_key = await AuthService.generate_api_key(target_name, role="user", db=db_session)

    # Get User IDs
    user_obj = (await db_session.execute(select(User).where(User.user_name == user_name))).scalar_one()
    target_user_obj = (await db_session.execute(select(User).where(User.user_name == target_name))).scalar_one()
    admin_obj = (await db_session.execute(select(User).where(User.user_name == admin_name))).scalar_one()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        # 2. Verify Initial Access Denied (No API Permission)
        # Note: verify_v1_api_access dependency checks for permission first.
        resp = await client.get(
            "/api/v1/users/profile",
            params={"username": target_name},
            headers={"X-API-Key": user_key}
        )
        # Should be 403 Forbidden because of missing V1 API permission
        assert resp.status_code == 403
        assert "Permission denied" in resp.text

        # 3. Grant API Permission to User
        # We need to grant 'GET:/api/v1/users/profile' in 'apis' list.
        # Use Admin to grant permission
        grant_payload = {
            "apis": ["GET:/api/v1/users/profile"]
        }
        resp = await client.put(
            f"/api/portal/management/users/{user_obj.id}/permissions",
            json=grant_payload,
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200

        # 4. Verify Access Allowed (Cross User)
        resp = await client.get(
            "/api/v1/users/profile",
            params={"username": target_name},
            headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        # Adjusted for StandardResponse wrapper
        assert data["data"]["username"] == target_name
        assert data["data"]["id"] == target_user_obj.id
        # Key should be visible (user -> user) or hidden? 
        # Current logic: only hide if target is admin. So user -> user shows key (per your instruction) or we default hide?
        # Assuming current logic only hides admin key.

        # 5. Verify Access Allowed (Self)
        resp = await client.get(
            "/api/v1/users/profile", 
            params={"username": user_name},
            headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"] == user_name
        # Self query should definitely show key
        assert data["data"]["api_key"] is not None

        # 6. Verify Access Allowed (Self - Default)
        resp = await client.get(
            "/api/v1/users/profile", 
            headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"] == user_name

        # 7. Verify Admin Access (Always allowed)
        resp = await client.get(
            "/api/v1/users/profile", 
            params={"username": user_name},
            headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"] == user_name
        
        # 8. Security Check: User querying Admin -> Should hide API Key
        resp = await client.get(
            "/api/v1/users/profile",
            params={"username": admin_name},
            headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["username"] == admin_name
        # Assert API Key is hidden/None
        assert data["data"]["api_key"] is None

    # Cleanup
    # Note: If tests fail before this, rows remain. 
    # Ideal: Use pytest fixture for cleanup. But inline here is okay for now.
    await db_session.delete(user_obj)
    await db_session.delete(target_user_obj)
    await db_session.delete(admin_obj)
    await db_session.commit()
