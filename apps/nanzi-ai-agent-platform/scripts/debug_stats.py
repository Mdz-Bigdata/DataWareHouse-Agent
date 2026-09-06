import asyncio
from app.core import database
from datetime import datetime, timedelta

async def debug_stats():
    await database.init_db()
    
    async with database.get_db_connection() as conn:
        async with conn.cursor() as cursor:
            # 1. 检查总数
            await cursor.execute("SELECT COUNT(*) FROM ai_agent_access_logs")
            access_count = (await cursor.fetchone())[0]
            
            await cursor.execute("SELECT COUNT(*) FROM ai_agent_execution_traces")
            trace_count = (await cursor.fetchone())[0]
            
            print(f"Total Access Logs: {access_count}")
            print(f"Total Execution Traces: {trace_count}")
            
            # 2. 检查今日数据 (UTC/Local 混合排查)
            now = datetime.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_str = today_start.strftime('%Y-%m-%d %H:%M:%S')
            
            print(f"\nChecking data since {today_start_str}...")
            
            await cursor.execute("SELECT COUNT(*) FROM ai_agent_access_logs WHERE created_at >= %s", (today_start_str,))
            access_today = (await cursor.fetchone())[0]
            
            await cursor.execute("SELECT COUNT(*) FROM ai_agent_execution_traces WHERE created_at >= %s", (today_start_str,))
            trace_today = (await cursor.fetchone())[0]
            
            print(f"Access Logs today: {access_today}")
            print(f"Execution Traces today: {trace_today}")
            
            # 3. 如果有数据，看几条详情
            if trace_today > 0:
                await cursor.execute("SELECT event_type, status, created_at FROM ai_agent_execution_traces ORDER BY created_at DESC LIMIT 5")
                rows = await cursor.fetchall()
                print("\nRecent Traces Detail:")
                for r in rows:
                    print(f" - {r[0]} | {r[1]} | {r[2]}")

    await database.close_db()

if __name__ == "__main__":
    asyncio.run(debug_stats())
