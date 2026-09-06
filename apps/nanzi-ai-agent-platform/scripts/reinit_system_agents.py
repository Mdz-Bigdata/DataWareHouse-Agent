import asyncio
import sys
import os
import aiomysql
import json

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings

SQL_FILE = "db-prod/V5_consolidated_agent_system.sql"

async def run_sql_script(pool, file_path):
    print(f"Applying SQL script: {file_path}")
    with open(file_path, 'r') as f:
        content = f.read()
    
    statements = content.split(';')
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            for stmt in statements:
                if stmt.strip():
                    try:
                        await cursor.execute(stmt)
                    except Exception as e:
                        print(f"SQL Error: {e}")
                        print(f"Statement: {stmt[:50]}...")

async def reinit_agents():
    print("🚀 Starting Refactored System Agent Initialization...")
    
    pool = await aiomysql.create_pool(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DB,
        autocommit=True
    )

    try:
        # 1. Cleaner Drop
        print("🗑️  Dropping existing agent tables...")
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DROP TABLE IF EXISTS ai_agent_versions")
                await cursor.execute("DROP TABLE IF EXISTS ai_agents")

        # 2. Re-create Tables
        print("🏗️  Re-creating tables from consolidated schema...")
        await run_sql_script(pool, SQL_FILE)

        # 3. Seed Agents (Handled by SQL file)
        print("🌱 Seeding agents via SQL...")
        # (Already executed in step 2 via run_sql_script)
        
        print("✅ System Agents Re-initialized Successfully!")
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(reinit_agents())
