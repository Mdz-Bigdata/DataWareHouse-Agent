"""Phase 3.4：数据源管理服务。

提供数据源 CRUD + 密码加密/脱敏 + 连通性测试。
密码用 Fernet 加密落库，API 返回时脱敏（绝不明文外泄）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.core.log import logger
from app.entities.datasource_info import DatasourceInfo
from app.models.mysql.datasource_mysql import DatasourceMySQL


class DatasourceService:
    """数据源 CRUD 与安全展示。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_datasources(self) -> list[DatasourceInfo]:
        """列出所有数据源（密码字段不解密，调用方按需脱敏）。"""

        result = await self.session.execute(select(DatasourceMySQL))
        return [self._to_entity(row) for row in result.scalars().all()]

    async def get_datasource(self, datasource_id: str) -> DatasourceInfo | None:
        row = await self.session.get(DatasourceMySQL, datasource_id)
        return self._to_entity(row) if row else None

    async def create_datasource(self, datasource: DatasourceInfo) -> DatasourceInfo:
        """创建数据源。password 明文传入，落库前加密。

        若 active=True，先把其他数据源置为 inactive（保证唯一 active）。
        """

        if datasource.active:
            await self._deactivate_all()
        row = DatasourceMySQL(
            id=datasource.id,
            name=datasource.name,
            dialect=datasource.dialect,
            host=datasource.host,
            port=datasource.port,
            database=datasource.database,
            user=datasource.user,
            password=encrypt_secret(datasource.password),
            active=datasource.active,
            description=datasource.description,
        )
        self.session.add(row)
        await self.session.commit()
        logger.info("创建数据源: id={} dialect={}", datasource.id, datasource.dialect)
        return self._to_entity(row)

    async def update_datasource(self, datasource_id: str, **fields) -> DatasourceInfo | None:
        """更新数据源。password 字段若传入明文则重新加密。

        active 置为 True 时先把其他数据源置为 inactive。
        """

        row = await self.session.get(DatasourceMySQL, datasource_id)
        if row is None:
            return None
        for key, value in fields.items():
            if key == "password" and value:
                # 明文密码：加密后落库
                row.password = encrypt_secret(value)
            elif key == "active" and value:
                await self._deactivate_all()
                row.active = True
            elif hasattr(row, key) and key != "id":
                setattr(row, key, value)
        await self.session.commit()
        logger.info("更新数据源: id={}", datasource_id)
        return self._to_entity(row)

    async def delete_datasource(self, datasource_id: str) -> bool:
        row = await self.session.get(DatasourceMySQL, datasource_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        logger.info("删除数据源: id={}", datasource_id)
        return True

    async def get_decrypted_password(self, datasource_id: str) -> str | None:
        """获取解密后的密码（仅内部使用，不暴露给 API 响应）。"""

        row = await self.session.get(DatasourceMySQL, datasource_id)
        if row is None:
            return None
        return decrypt_secret(row.password)

    async def _deactivate_all(self) -> None:
        """把所有数据源的 active 置为 False。"""

        result = await self.session.execute(
            select(DatasourceMySQL).where(DatasourceMySQL.active == True)  # noqa: E712
        )
        for row in result.scalars().all():
            row.active = False

    def _to_entity(self, row: DatasourceMySQL) -> DatasourceInfo:
        return DatasourceInfo(
            id=row.id,
            name=row.name,
            dialect=row.dialect,
            host=row.host,
            port=row.port,
            database=row.database,
            user=row.user,
            password=row.password,  # ORM 里是密文；展示时调用方负责脱敏
            active=row.active,
            description=row.description or "",
        )


def mask_datasource_password(datasource: DatasourceInfo) -> str:
    """脱敏展示密码（API 响应专用）。"""

    try:
        plaintext = decrypt_secret(datasource.password)
        return mask_secret(plaintext)
    except ValueError:
        # 解密失败（密钥变更等）返回固定掩码
        return "****（无法解密）"
