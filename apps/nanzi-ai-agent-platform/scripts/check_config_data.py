import asyncio
import os
import aiomysql
from dotenv import load_dotenv

load_dotenv()

async def check_data():
    host = os.getenv("MYSQL_HOST", "localhost")
    port = int(os.getenv("MYSQL_PORT", 3306))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("MYSQL_DB", "")

    print(f"🔌 Connecting to {user}@{host}:{port}/{db}...")
    try:
        pool = await aiomysql.create_pool(
            host=host, port=port,
            user=user, password=password,
            db=db, autocommit=True
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT `key`, `value`, `description`, `category`, `is_secret` FROM system_configs")
                rows = await cur.fetchall() # Wait, check_config_data used cur.fetchall()
                # But in previous code `check_config_data.py` I wrote `await cur.execute("SELECT key, value, category FROM...")`
                # Let's verify what I wrote to check_config_data.py in Step 337.
                # It was `SELECT key, value, category FROM system_configs`
                # So I will update it to select *all* relevant columns to debug.
                print(f"Found {len(rows)} configs:")
                for row in rows:
                     val = row[1]
                     if val and len(val) > 10:
                        val_preview = val[:5] + "..." + val[-5:]
                     else:
                        val_preview = val
                     print(f"Key: {row[0]}, Value: {val_preview}")
        pool.close()
        await pool.wait_closed()
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_data())
