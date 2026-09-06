import asyncio
import httpx
import json
import sys
import os

# 确保能导入 app
sys.path.append(os.getcwd())

async def test_verify_api():
    url = "http://localhost:8001/api/portal/mcp/verify"
    target_mcp = "https://mcp.api-inference.modelscope.net/1662c45d70a244/mcp"
    
    print(f"\n[API Test] Target: {url}")
    
    # 1. 尝试加载所有模型以避免 SQLAlchemy 映射错误
    from app.models import user, permission, agent, metadata, tool, mcp
    from app.core.orm import AsyncSessionLocal
    from app.models.user import User
    from sqlalchemy import select
    from app.services.auth_service import AuthService

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.role == 'admin'))
            admin = result.scalars().first()
            if not admin:
                print("Error: No admin user found")
                return
            admin_key = await AuthService.get_decrypted_api_key(admin.id, session)
    except Exception as e:
        print(f"❌ DB Initialization Error: {e}")
        return

    print(f"[Auth] Using Admin: {admin.user_name}")

    # 2. 发起 API 请求
    headers = {
        "X-API-Key": admin_key,
        "Content-Type": "application/json"
    }
    payload = {
        "server_name": "test-wizard-mcp",
        "sse_url": target_mcp,
        "auth_headers": "{}",
        "enabled_status": 1
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            print(f"[Action] Sending verification request...")
            resp = await client.post(url, json=payload, headers=headers)
            print(f"Status Code: {resp.status_code}")
            
            data = resp.json()
            if resp.status_code == 200:
                print("✅ API SUCCESS!")
                print(f"Discovered Tools: {len(data.get('tools', []))}")
                for t in data.get('tools', [])[:3]:
                    print(f"  - {t['name']}")
            else:
                print("❌ API FAILURE")
                print("Message:", data.get('message') or data.get('detail'))
        except Exception as e:
            print(f"❌ Request Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_verify_api())
 Joe