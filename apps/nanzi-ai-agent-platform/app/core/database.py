from sqlalchemy import text
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from app.core.orm import engine
import logging

# Configure logger
logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_db_connection():
    """
    Get a raw MySQL connection from the SQLAlchemy engine's pool.
    This consolidates connection management into a single pool.
    """
    async with engine.connect() as conn:
        # Get the underlying aiomysql connection
        raw_conn = await conn.get_raw_connection()
        # aiomysql connection is already a context manager in some versions, 
        # but here we just yield it because SQLAlchemy manages the lifecycle of 'conn'.
        yield raw_conn

async def init_db():
    """
    Ping the database to ensure connection is valid.
    The pool is managed by SQLAlchemy engine.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("✅ Database health check passed (via SQLAlchemy Engine)")
    except Exception as e:
        logger.error(f"❌ Database health check failed: {e}")
        raise

async def close_db():
    """
    Dispose the SQLAlchemy engine.
    """
    await engine.dispose()
    logger.info("✅ Database engine disposed")