import asyncio
import uuid
import os
import sys
import logging
from datetime import datetime

# Ensure app path
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer, McpToolCache
from app.services.ai.tools.mcp_client import McpClientService
from sqlalchemy import select, delete

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_mcp_flow")

async def test_full_sync_flow():
    # 1. Setup Test Data
    # ---------------------------------------------------
    server_id = str(uuid.uuid4())
    test_url = "https://mcp.api-inference.modelscope.net/3af97f57a7bd4d/mcp"
    test_token = os.environ.get("MCP_TOKEN", "")
    
    if not test_token:
        print("❌ Error: MCP_TOKEN environment variable is required.")
        return

    print("\n🚀 [TEST] Starting Full Sync Flow Test")
    print(f"Target Server ID: {server_id}")
    
    async with AsyncSessionLocal() as session:
        # Create a mock McpServer record
        new_server = McpServer(
            id=server_id,
            server_name="Test_ModelScope_Server",
            # server_type="sse",  <-- Removed invalid field
            sse_url=test_url,
            auth_headers=f'{{"Authorization": "Bearer {test_token}"}}',
            enabled_status=1,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        session.add(new_server)
        await session.commit()
        print("✅ [DB] Created test McpServer record.")

    try:
        # 2. Trigger Sync Logic (Simulating the API call)
        # ---------------------------------------------------
        print("\n🔄 [ACTION] Triggering McpClientService.sync_tools()...")
        await McpClientService.sync_tools(server_id)
        print("✅ [ACTION] Sync function returned without error.")

        # 3. Verify Results in DB
        # ---------------------------------------------------
        async with AsyncSessionLocal() as session:
            stmt = select(McpToolCache).where(McpToolCache.server_id == server_id)
            result = await session.execute(stmt)
            tools = result.scalars().all()
            
            if tools:
                print(f"\n🎉 [VERIFY] Success! Found {len(tools)} synced tools in DB:")
                for t in tools:
                    print(f"   - 🛠️ {t.tool_name} (ID: {t.id})")
            else:
                print("\n❌ [VERIFY] Failed: No tools found in DB after sync.")

    except Exception as e:
        print(f"\n❌ [ERROR] Exception during sync flow: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 4. Cleanup
        # ---------------------------------------------------
        print("\n🧹 [CLEANUP] Removing test data...")
        async with AsyncSessionLocal() as session:
            await session.execute(delete(McpToolCache).where(McpToolCache.server_id == server_id))
            await session.execute(delete(McpServer).where(McpServer.id == server_id))
            await session.commit()
        
        # Close connection session
        if server_id in McpClientService._sessions:
            await McpClientService._sessions[server_id].close()
            
        print("✅ Cleanup complete.")

if __name__ == "__main__":
    asyncio.run(test_full_sync_flow())