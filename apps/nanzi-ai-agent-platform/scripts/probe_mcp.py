import asyncio
import httpx
import json
import time

async def probe_mcp(url):
    print(f"\n[Probe] Target: {url}")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Initialize
        print("\n[Step 1] Initializing...")
        init_payload = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "probe", "version": "1.0.0"}
            }
        }
        resp = await client.post(url, json=init_payload, headers=headers)
        s_id = resp.headers.get("mcp-session-id") or resp.json().get("result", {}).get("session_id")
        print(f"Captured Session ID: {s_id}")

        # Test tool list with Query Param injection
        print(f"\n[Step 2] Testing tools/list with Query Param...")
        headers["mcp-session-id"] = s_id
        target_with_query = f"{url}?sessionId={s_id}" # Common gateway pattern
        
        payload = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
        }
        
        try:
            r = await client.post(target_with_query, json=payload, headers=headers)
            print(f"Status: {r.status_code}")
            print(f"Body: {r.text}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    target = "https://mcp.api-inference.modelscope.net/1662c45d70a244/mcp"
    asyncio.run(probe_mcp(target))
