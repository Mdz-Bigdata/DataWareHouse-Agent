"""AuthService 的功能测试：sqlite 内存库 + 与应用一致的 sessionmaker 配置。

回归背景：线上登录曾因为 commit 后访问过期 ORM 属性抛 MissingGreenlet（500），
根因是 sessionmaker 未设 expire_on_commit=False。这里用真实数据库验证该行为。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.mysql.base import Base
from app.models.mysql.user_mysql import UserMySQL
from app.services.auth_service import AuthService, ensure_admin_seed


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # 与应用工厂相同的配置（见 mysql_client_manager）
    factory = async_sessionmaker(
        engine,
        autoflush=False,
        autobegin=True,
        autocommit=False,
        expire_on_commit=False,
    )
    yield factory
    await engine.dispose()


async def _add_user(factory, **overrides) -> str:
    data = {
        "id": str(uuid.uuid4()),
        "username": "tester",
        "password_hash": hash_password("pass123"),
        "role": "user",
        "must_change_password": False,
    }
    data.update(overrides)
    async with factory() as session:
        session.add(UserMySQL(**data))
        await session.commit()
    return data["id"]


@pytest.mark.asyncio
async def test_verify_login_returns_usable_user_after_commit(session_factory):
    await _add_user(session_factory)
    async with session_factory() as session:
        user = await AuthService(session).verify_login("tester", "pass123")
        assert user is not None
        # commit 之后继续访问属性（登录接口签发 token 的场景）不得触发隐式 IO
        assert user.username == "tester"
        assert user.id
        assert user.last_login_at is not None


@pytest.mark.asyncio
async def test_verify_login_rejects_wrong_password_and_unknown_user(session_factory):
    await _add_user(session_factory)
    async with session_factory() as session:
        service = AuthService(session)
        assert await service.verify_login("tester", "bad-pass") is None
        assert await service.verify_login("nobody", "pass123") is None


@pytest.mark.asyncio
async def test_change_password_flow(session_factory):
    user_id = await _add_user(session_factory, must_change_password=True)
    async with session_factory() as session:
        service = AuthService(session)
        user = await service.get_user_by_id(user_id)
        assert await service.change_password(user, "wrong-old", "newpass1") is False
        assert await service.change_password(user, "pass123", "newpass1") is True
    async with session_factory() as session:
        service = AuthService(session)
        assert await service.verify_login("tester", "newpass1") is not None
        assert await service.verify_login("tester", "pass123") is None


@pytest.mark.asyncio
async def test_admin_seed_is_idempotent(session_factory):
    async with session_factory() as session:
        await ensure_admin_seed(session)
    async with session_factory() as session:
        await ensure_admin_seed(session)  # 第二次不再播种
    async with session_factory() as session:
        service = AuthService(session)
        admin = await service.verify_login("admin", "admin123")
        assert admin is not None
        assert admin.role == "admin"
        assert admin.must_change_password is True


def test_app_session_factory_disables_expire_on_commit():
    """应用自身的 session 工厂必须关闭 expire_on_commit（登录 500 回归）。"""
    from app.clients.mysql_client_manager import meta_mysql_client_manager

    meta_mysql_client_manager.init_client()
    assert meta_mysql_client_manager.session_factory is not None
    assert meta_mysql_client_manager.session_factory.kw.get("expire_on_commit") is False
