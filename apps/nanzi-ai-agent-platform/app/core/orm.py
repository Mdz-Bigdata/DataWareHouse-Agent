from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings
from typing import AsyncGenerator

class Base(DeclarativeBase):
    pass

# SQLAlchemy Async Engine
engine = create_async_engine(
    f"mysql+aiomysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DB}",
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.MYSQL_POOL_SIZE,
    max_overflow=settings.MYSQL_MAX_OVERFLOW,
    pool_recycle=settings.MYSQL_POOL_RECYCLE
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
