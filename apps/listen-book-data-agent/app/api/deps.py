"""API 层公共依赖：当前登录用户解析与角色守卫。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import get_meta_session
from app.core.security import decode_access_token
from app.models.mysql.user_mysql import UserMySQL
from app.services.auth_service import AuthService

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或登录已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
) -> UserMySQL:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    payload = decode_access_token(credentials.credentials)
    if payload is None or not payload.get("sub"):
        raise _UNAUTHORIZED
    user = await AuthService(meta_session).get_user_by_id(payload["sub"])
    if user is None:
        raise _UNAUTHORIZED
    return user


async def require_admin(
    user: Annotated[UserMySQL, Depends(get_current_user)],
) -> UserMySQL:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限"
        )
    return user
