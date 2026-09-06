
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from app.core.orm import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
        print("--- Checking Collation ---")
        
        # Check ai_agents
        res = await session.execute(text("SHOW FULL COLUMNS FROM ai_agents"))
        print("\nColumns in 'ai_agents':")
        for row in res.fetchall():
            print(f"  {row.Field}: {row.Type}, Collation: {row.Collation}")
            
        # Check ai_agent_execution_history
        res = await session.execute(text("SHOW FULL COLUMNS FROM ai_agent_execution_history"))
        print("\nColumns in 'ai_agent_execution_history':")
        for row in res.fetchall():
            print(f"  {row.Field}: {row.Type}, Collation: {row.Collation}")

if __name__ == "__main__":
    asyncio.run(check())
