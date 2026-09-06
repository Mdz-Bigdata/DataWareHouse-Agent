import asyncio
import logging
import sys
import os

# Ensure app path is in sys.path
sys.path.append(os.getcwd())

from app.services.ai.tools.mcp_client import McpSseSession, McpClientService

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.services.ai.tools.mcp_client")
logger.setLevel(logging.DEBUG)

async def test_mcp_client():
    url = "https://mcp.api-inference.modelscope.net/1662c45d70a244/mcp"
    temp_id = "test_session_modelscope"
    
    print(f"\n🚀 Testing McpSseSession with: {url}")
    print("-" * 60)

    # 1. Manually setup session in service cache
    session_mgr = McpSseSession(temp_id, url, {})
    McpClientService._sessions[temp_id] = session_mgr
    
    try:
        # 2. Test list_remote_tools
        print("\n[Action] Calling list_remote_tools()...")
        tools = await McpClientService.list_remote_tools(temp_id)
        
        print("\n✅ Success! Discovered Tools:")
        for t in tools:
            name = t.name if hasattr(t, 'name') else t.get('name')
            print(f"- {name}")
            
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        await session_mgr.close()

if __name__ == "__main__":
    asyncio.run(test_mcp_client())