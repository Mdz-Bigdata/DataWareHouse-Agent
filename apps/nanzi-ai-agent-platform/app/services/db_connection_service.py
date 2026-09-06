from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List, Optional

from app.models.db_connection import MetaDbConnectionConfig


class DbConnectionService:
    """数据库连接配置 CRUD 服务"""

    @staticmethod
    async def list_configs(conn: AsyncSession) -> List[MetaDbConnectionConfig]:
        """获取所有连接配置（所有有权限的用户共享）"""
        result = await conn.execute(
            select(MetaDbConnectionConfig).order_by(MetaDbConnectionConfig.updated_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def get_config(conn: AsyncSession, config_id: int) -> Optional[MetaDbConnectionConfig]:
        """按 ID 获取连接配置"""
        result = await conn.execute(
            select(MetaDbConnectionConfig).where(MetaDbConnectionConfig.id == config_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_config_by_name(conn: AsyncSession, name: str) -> Optional[MetaDbConnectionConfig]:
        """按名称获取连接配置"""
        result = await conn.execute(
            select(MetaDbConnectionConfig).where(MetaDbConnectionConfig.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_config(
        conn: AsyncSession,
        data: dict,
        user_id: int = 0
    ) -> MetaDbConnectionConfig:
        """创建一条连接配置"""
        existing = await DbConnectionService.get_config_by_name(conn, data["name"])
        if existing:
            raise ValueError("数据源名称已存在")

        config = MetaDbConnectionConfig(
            name=data["name"],
            db_type=data["db_type"],
            host=data["host"],
            port=data["port"],
            db_user=data["db_user"],
            password=data["password"],
            database_name=data["database_name"],
            description=data.get("description", ""),
            created_by=user_id,
        )
        conn.add(config)
        await conn.commit()
        await conn.refresh(config)
        return config

    @staticmethod
    async def update_config(
        conn: AsyncSession,
        config_id: int,
        data: dict,
    ) -> Optional[MetaDbConnectionConfig]:
        """更新连接配置"""
        config = await DbConnectionService.get_config(conn, config_id)
        if not config:
            return None

        existing = await DbConnectionService.get_config_by_name(conn, data["name"])
        if existing and existing.id != config_id:
            raise ValueError("数据源名称已存在")

        config.name = data["name"]
        config.db_type = data["db_type"]
        config.host = data["host"]
        config.port = data["port"]
        config.db_user = data["db_user"]
        config.password = data["password"]
        config.database_name = data["database_name"]
        config.description = data.get("description", "")

        await conn.commit()
        await conn.refresh(config)
        return config

    @staticmethod
    async def delete_config(conn: AsyncSession, config_id: int) -> bool:
        """删除指定连接配置，返回是否删除成功"""
        result = await conn.execute(
            delete(MetaDbConnectionConfig).where(MetaDbConnectionConfig.id == config_id)
        )
        await conn.commit()
        return result.rowcount > 0
