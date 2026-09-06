import asyncio
import httpx
import json

async def test_list_ragflow_agents(url, api_key):
    """
    Test script to verify listing RAGFlow agents via GET /api/v1/agents.
    """
    base_url = url.rstrip("/")
    list_url = f"{base_url}/api/v1/agents"
    
    print(f"🔍 Testing List RAGFlow Agents")
    print(f"📡 URL: {list_url}")
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(list_url, headers=headers)
            print(f"   -> Status Code: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                print(f"   ✅ SUCCESS! Found {len(data.get('data', []))} agents.")
                print(f"   -> Response: {json.dumps(data, ensure_ascii=False, indent=2)}")
            else:
                print(f"   ❌ Failed: {resp.status_code}")
                print(f"   Response: {resp.text}")
                
        except Exception as e:
            print(f"   ❌ Exception: {str(e)}")

if __name__ == "__main__":
    RAGFLOW_URL = "http://175.102.130.108"
    RAGFLOW_KEY = "ragflow-QHX513dmfqJunoOZOKGq8hJAAxKOqeHmqzuXG7oyTcY"
    
    asyncio.run(test_list_ragflow_agents(RAGFLOW_URL, RAGFLOW_KEY))
