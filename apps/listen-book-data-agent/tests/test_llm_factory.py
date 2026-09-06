"""get_llm 热切换测试：启用配置解析、缓存与指纹失效。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.agent.llm as llm_module
from app.core.crypto import encrypt_secret
from app.models.mysql.base import Base
from app.models.mysql.llm_provider_mysql import LlmProviderMySQL


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        autoflush=False,
        autobegin=True,
        autocommit=False,
        expire_on_commit=False,
    )
    yield factory
    await engine.dispose()


@pytest.fixture
def patched(monkeypatch, session_factory):
    """把 meta 客户端指向 sqlite，build_chat_model 打桩为参数回显。"""
    monkeypatch.setattr(
        llm_module,
        "meta_mysql_client_manager",
        SimpleNamespace(session_factory=session_factory),
    )
    monkeypatch.setattr(llm_module, "build_chat_model", lambda **kwargs: kwargs)
    llm_module._llm_cache.clear()
    yield session_factory
    llm_module._llm_cache.clear()


async def _seed(
    factory, *, active: bool, model: str = "deepseek-chat"
) -> LlmProviderMySQL:
    provider = LlmProviderMySQL(
        id=str(uuid.uuid4()),
        name="测试",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        model_name=model,
        api_key_encrypted=encrypt_secret("sk-db-key-0001"),
        temperature=0.0,
        timeout_seconds=45,
        is_active=active,
    )
    async with factory() as session:
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
    return provider


@pytest.mark.asyncio
async def test_env_fallback_when_table_empty(patched):
    from app.conf.app_config import app_config

    config = await llm_module.get_llm()
    assert config["provider_type"] == app_config.llm.provider
    assert config["model_name"] == app_config.llm.model_name
    assert config["api_key"] != "sk-db-key-0001"  # 来自环境变量配置


@pytest.mark.asyncio
async def test_active_provider_wins_and_decrypts_key(patched):
    await _seed(patched, active=True)
    config = await llm_module.get_llm()
    assert config["api_key"] == "sk-db-key-0001"
    assert config["timeout_seconds"] == 45


@pytest.mark.asyncio
async def test_client_cached_until_config_changes(patched):
    provider = await _seed(patched, active=True)
    first = await llm_module.get_llm()
    second = await llm_module.get_llm()
    assert first is second  # 同配置复用客户端

    async with patched() as session:
        row = await session.get(LlmProviderMySQL, provider.id)
        row.model_name = "deepseek-reasoner"
        await session.commit()
    third = await llm_module.get_llm()
    assert third is not first  # 指纹变化触发重建
    assert third["model_name"] == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_switch_active_provider_takes_effect_without_restart(patched):
    # 初始活跃 provider 作为「切换前」基线；后续用全表 UPDATE 切换 active，
    # 不需要其返回 id 定位，故不入变量（避免 F841）。
    await _seed(patched, active=True, model="deepseek-chat")
    new = await _seed(patched, active=False, model="deepseek-reasoner")

    before = await llm_module.get_llm()
    assert before["model_name"] == "deepseek-chat"

    async with patched() as session:
        from sqlalchemy import update

        await session.execute(update(LlmProviderMySQL).values(is_active=False))
        row = await session.get(LlmProviderMySQL, new.id)
        row.is_active = True
        await session.commit()

    after = await llm_module.get_llm()
    assert after["model_name"] == "deepseek-reasoner"
    assert after is not before
