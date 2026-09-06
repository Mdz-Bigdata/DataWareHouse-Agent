"""用户与认证业务逻辑。当前仅包含用户查询与管理员播种；登录签发见 auth_router。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conf.app_config import app_config
from app.core.log import logger
from app.core.security import hash_password, verify_password
from app.models.mysql.user_mysql import UserMySQL


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_id(self, user_id: str) -> UserMySQL | None:
        return await self.session.get(UserMySQL, user_id)

    async def get_user_by_username(self, username: str) -> UserMySQL | None:
        result = await self.session.execute(
            select(UserMySQL).where(UserMySQL.username == username)
        )
        return result.scalar_one_or_none()

    async def verify_login(self, username: str, password: str) -> UserMySQL | None:
        """校验口令，成功时刷新 last_login_at。失败统一返回 None，不区分用户不存在。"""
        user = await self.get_user_by_username(username)
        if user is None or not verify_password(password, user.password_hash):
            return None
        user.last_login_at = datetime.now()
        await self.session.commit()
        return user

    async def change_password(
        self, user: UserMySQL, old_password: str, new_password: str
    ) -> bool:
        if not verify_password(old_password, user.password_hash):
            return False
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        await self.session.commit()
        return True


async def ensure_admin_seed(session: AsyncSession) -> None:
    """users 表为空时播种默认管理员（admin/admin123，强制首登改密）。"""
    result = await session.execute(select(sa_func.count()).select_from(UserMySQL))
    if result.scalar_one() > 0:
        return
    admin = UserMySQL(
        id=str(uuid.uuid4()),
        username=app_config.auth.admin_username,
        password_hash=hash_password(app_config.auth.admin_password),
        role="admin",
        must_change_password=True,
    )
    session.add(admin)
    await session.commit()
    logger.info("users 表为空，已播种管理员账号 {}", app_config.auth.admin_username)
