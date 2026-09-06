import asyncio
import logging
import sys
import os
import json

# Ensure app path is in sys.path
sys.path.append(os.getcwd())

from app.services.ai.tools.mcp_client import McpSseSession, McpClientService

# Setup detailed logging to console
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("app.services.ai.tools.mcp_client")

async def debug_modelscope_sync():
    # 使用用户提供的 URL
    url = "https://mcp.api-inference.modelscope.net/3af97f57a7bd4d/mcp"
    temp_id = "debug_modelscope_server"
    
    # 从环境变量获取 Token
    token = os.environ.get("MCP_TOKEN", "")
    
    auth_headers = {
        "Authorization": f"Bearer {token}"
    }
    
    print("\n🚀 [DEBUG] Testing ModelScope MCP Connection")
    print(f"📍 URL: {url}")
    print(f"🔑 Header Keys: {list(auth_headers.keys())}")
    print("-" * 60)

    # 1. Manually setup session in service cache
    session_mgr = McpSseSession(temp_id, url, auth_headers)
    McpClientService._sessions[temp_id] = session_mgr
    
    try:
        # 2. Test list_remote_tools
        print("\n[Action] Calling list_remote_tools()...")
        tools = await McpClientService.list_remote_tools(temp_id)
        
        print(f"\n✅ [SUCCESS] Discovered {len(tools)} Tools:")
        for t in tools:
            name = t.name if hasattr(t, 'name') else t.get('name', 'Unknown')
            desc = t.description if hasattr(t, 'description') else t.get('description', 'No description')
            print(f"  - {name}: {desc[:50]}...")
            
    except Exception as e:
        print(f"\n❌ [FAILED] Error during synchronization: {str(e)}")
        
    finally:
        await session_mgr.close()
        print("\n[Done] Session closed.")

if __name__ == "__main__":
    asyncio.run(debug_modelscope_sync())