import asyncio
import asynch
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_connection():
    config = {
        'host': 'localhost',
        'port': 9000,
        'user': 'admin',
        'password': 'admin123',
        'database': 'yovole_ck_prod'
    }
    
    logger.info(f"Testing ClickHouse connection to {config['host']}:{config['port']} (DB: {config['database']})")
    
    try:
        conn = asynch.Connection(
            host=config['host'],
            port=config['port'],
            user=config['user'],
            password=config['password'],
            database=config['database'],
            connect_timeout=10
        )
        await conn.connect()
        logger.info("✅ Connection established successfully!")
        
        async with conn.cursor() as cur:
            await cur.execute("SELECT version()")
            res = await cur.fetchone()
            logger.info(f"ClickHouse Version: {res[0]}")
            
            await cur.execute("SHOW TABLES")
            tables = await cur.fetchall()
            logger.info(f"Found {len(tables)} tables in {config['database']}")
            for t in tables[:5]:
                logger.info(f"  - {t[0]}")
                
        await conn.close()
        logger.info("Connection closed.")
        return True
    except Exception as e:
        logger.error(f"❌ Connection FAILED: {str(e)}")
        return False

if __name__ == "__main__":
    asyncio.run(test_connection())
