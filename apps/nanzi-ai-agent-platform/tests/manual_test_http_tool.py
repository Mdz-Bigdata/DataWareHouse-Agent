import asyncio
import sys
import os

# Ensure the correct python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai.tools.registry import ToolRegistry

async def test_system_http_request():
    print("🚀 Testing system_http_request tool...")

    # 1. Get the tool
    tool = await ToolRegistry.get_tool("system_http_request")
    if not tool:
        print("❌ Error: Tool 'system_http_request' not found in registry.")
        return

    print("✅ Tool found in registry.")

    # 2. Test Safe Request (GET httpbin.org)
    print("\n[Test 1] Safe Request (GET httpbin.org/get)")
    try:
        result = await tool.ainvoke({
            "method": "GET",
            "url": "https://httpbin.org/get",
            "params": {"test": "123"}
        })
        print(f"Result: {result[:200]}...") # Print first 200 chars
        if '"url":' in result or '"args":' in result:
             print("✅ Success: Safe request executed and returned JSON.")
        else:
             print("❌ Failure: Expected JSON response from httpbin.")
    except Exception as e:
        print(f"❌ Failed: {e}")

    # 3. Test Blocked Request (Localhost)
    print("\n[Test 2] Blocked Request (GET localhost)")
    try:
        result = await tool.ainvoke({
            "method": "GET",
            "url": "http://localhost:8001/health"
        })
        if "Error executing request" in result and "restricted" in result:
            print(f"✅ Success: Localhost request blocked correctly. Msg: {result}")
        else:
            print(f"❌ Specific Error Expected, got: {result}")
    except Exception as e:
        print(f"❌ Failed (Unexpected Exception): {e}")

    # 4. Test Blocked Request (127.0.0.1)
    print("\n[Test 3] Blocked Request (GET 127.0.0.1)")
    try:
        result = await tool.ainvoke({
            "method": "GET",
            "url": "http://127.0.0.1:6379"
        })
        if "Error executing request" in result and "restricted" in result:
             print(f"✅ Success: 127.0.0.1 request blocked correctly. Msg: {result}")
        else:
             print(f"❌ Specific Error Expected, got: {result}")
    except Exception as e:
        print(f"❌ Failed (Unexpected Exception): {e}")

if __name__ == "__main__":
    asyncio.run(test_system_http_request())
