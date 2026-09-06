from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import get_meta_session
from app.api.deps import get_current_user
from app.api.schemas.auth_schema import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserInfo,
)
from app.conf.app_config import app_config
from app.core.rate_limit import limiter, rate_limit_login
from app.core.security import create_access_token
from app.models.mysql.user_mysql import UserMySQL
from app.services.auth_service import AuthService

auth_router = APIRouter(tags=["认证模块"])


def _to_user_info(user: UserMySQL) -> UserInfo:
    return UserInfo(
        id=user.id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
    )


@auth_router.post("/api/auth/login", response_model=LoginResponse)
@limiter.limit(rate_limit_login())
async def login(
    request: Request,
    body: LoginRequest,
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = AuthService(meta_session)
    user = await service.verify_login(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(user_id=user.id, username=user.username, role=user.role)
    return LoginResponse(
        token=token,
        expires_in_minutes=app_config.auth.token_ttl_minutes,
        user=_to_user_info(user),
    )


@auth_router.post("/api/auth/logout")
async def logout(user: Annotated[UserMySQL, Depends(get_current_user)]):
    """无状态 JWT 由客户端丢弃令牌，此端点仅用于统一交互与后续扩展。"""
    return {"status": "ok"}


@auth_router.get("/api/auth/me", response_model=UserInfo)
async def me(user: Annotated[UserMySQL, Depends(get_current_user)]):
    return _to_user_info(user)


@auth_router.post("/api/auth/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: Annotated[UserMySQL, Depends(get_current_user)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = AuthService(meta_session)
    ok = await service.change_password(user, body.old_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="原密码错误")
    return {"status": "ok"}
