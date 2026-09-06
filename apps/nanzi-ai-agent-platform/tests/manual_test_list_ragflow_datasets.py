import asyncio
import httpx
import json

async def test_list_ragflow_datasets(url, api_key):
    """
    Test script to verify listing RAGFlow datasets via GET /api/v1/datasets.
    """
    base_url = url.rstrip("/")
    list_url = f"{base_url}/api/v1/datasets"
    
    print(f"🔍 Testing List RAGFlow Datasets")
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
                print(f"   ✅ SUCCESS! Found {len(data.get('data', []))} datasets.")
                # Print simplified dataset info
                if data.get('data'):
                    for ds in data['data']:
                        print(f"      - ID: {ds.get('id')}, Name: {ds.get('name')}")
                else:
                    print("      (Empty list)")
            else:
                print(f"   ❌ Failed: {resp.status_code}")
                print(f"   Response: {resp.text}")
                
        except Exception as e:
            print(f"   ❌ Exception: {str(e)}")

if __name__ == "__main__":
    RAGFLOW_URL = "http://175.102.130.108"
    RAGFLOW_KEY = "ragflow-QHX513dmfqJunoOZOKGq8hJAAxKOqeHmqzuXG7oyTcY"
    
    asyncio.run(test_list_ragflow_datasets(RAGFLOW_URL, RAGFLOW_KEY))
