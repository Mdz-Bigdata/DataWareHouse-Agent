import asyncio
import json
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from app.models.tool import SysApiTool
from app.services.ai.tools.registry import ToolRegistry
from sqlalchemy import select

async def test_generic_tool():
    tool_name = "test_httpbin_ip"
    
    async with AsyncSessionLocal() as session:
        # 1. Create a tool in DB
        
        # Check if exists and delete
        stmt = select(SysApiTool).where(SysApiTool.name == tool_name)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            await session.delete(existing)
            await session.commit()
            print(f"Deleted existing {tool_name}")
            
        new_tool = SysApiTool(
            id="test-tool-id-123",
            name=tool_name,
            description="Get Origin IP from httpbin",
            method="GET",
            url_template="https://httpbin.org/ip",
            headers=None,
            parameter_schema=json.dumps({"dummy": {"type": "string", "required": False}}),
            is_active=True
        )
        session.add(new_tool)
        await session.commit()
        print(f"Created {tool_name}")

    # 2. Test Registry Fetch
    # Force generic factory usage by name
    tool = await ToolRegistry.get_tool(tool_name)
    if not tool:
        print("❌ Failed to fetch tool from registry")
        return
    
    print(f"✅ Fetched tool: {tool.name}")
    print(f"   Description: {tool.description}")
    print(f"   Args Schema: {tool.args_schema.schema()}")
    
    # 3. Test Execution
    print("Executing tool...")
    try:
        # ainvoke expects a dict matching the schema
        result = await tool.ainvoke({"dummy": "test"})
        print(f"✅ Result: {result}")
        
        if "origin" in result:
             print("Content validation passed.")
        else:
             print("Content validation warning: 'origin' not found in response.")
             
    except Exception as e:
        print(f"❌ Execution failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_generic_tool())
