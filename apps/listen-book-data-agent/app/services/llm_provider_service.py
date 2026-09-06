"""LLM 供应商配置业务逻辑：落库加密、唯一启用、连接测试、启动播种。"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import func as sa_func
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import build_chat_model
from app.api.schemas.llm_provider_schema import LlmProviderUpsert
from app.conf.app_config import app_config
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.log import logger
from app.models.mysql.llm_provider_mysql import LlmProviderMySQL


class LlmProviderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[LlmProviderMySQL]:
        result = await self.session.execute(
            select(LlmProviderMySQL).order_by(LlmProviderMySQL.created_at)
        )
        return list(result.scalars().all())

    async def get(self, provider_id: str) -> LlmProviderMySQL | None:
        return await self.session.get(LlmProviderMySQL, provider_id)

    async def get_active(self) -> LlmProviderMySQL | None:
        result = await self.session.execute(
            select(LlmProviderMySQL).where(LlmProviderMySQL.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def create(self, data: LlmProviderUpsert) -> LlmProviderMySQL:
        if not data.api_key:
            raise ValueError("新增供应商必须提供 API Key")
        provider = LlmProviderMySQL(
            id=str(uuid.uuid4()),
            name=data.name,
            provider_type=data.provider_type,
            base_url=data.base_url,
            model_name=data.model_name,
            api_key_encrypted=encrypt_secret(data.api_key),
            temperature=data.temperature,
            timeout_seconds=data.timeout_seconds,
            is_active=False,
        )
        self.session.add(provider)
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def update(
        self, provider: LlmProviderMySQL, data: LlmProviderUpsert
    ) -> LlmProviderMySQL:
        provider.name = data.name
        provider.provider_type = data.provider_type
        provider.base_url = data.base_url
        provider.model_name = data.model_name
        provider.temperature = data.temperature
        provider.timeout_seconds = data.timeout_seconds
        if data.api_key:  # 留空表示保持原密钥
            provider.api_key_encrypted = encrypt_secret(data.api_key)
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def delete(self, provider: LlmProviderMySQL) -> None:
        if provider.is_active:
            raise ValueError("启用中的供应商不能删除，请先启用其他供应商")
        await self.session.delete(provider)
        await self.session.commit()

    async def activate(self, provider: LlmProviderMySQL) -> None:
        """全表唯一启用：先清除其他记录的启用标记，再启用目标。"""
        await self.session.execute(
            update(LlmProviderMySQL).values(is_active=False)
        )
        provider.is_active = True
        await self.session.commit()
        await self.session.refresh(provider)

    def decrypt_api_key(self, provider: LlmProviderMySQL) -> str:
        return decrypt_secret(provider.api_key_encrypted)

    @staticmethod
    async def test_connection(
        *,
        provider_type: str,
        base_url: str,
        model_name: str,
        api_key: str,
        temperature: float,
        timeout_seconds: int,
    ) -> tuple[bool, int | None, str | None]:
        """发一条极短消息验证连通性，返回 (是否成功, 延迟ms, 错误信息)。"""
        try:
            llm = build_chat_model(
                provider_type=provider_type,
                model_name=model_name,
                api_key=api_key,
                base_url=base_url,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
            started_at = time.perf_counter()
            await llm.ainvoke("回复 OK")
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            return True, latency_ms, None
        except Exception as exc:  # 网络、鉴权、模型不存在等统一归类为连接失败
            logger.warning("LLM 连接测试失败：{}", exc)
            return False, None, str(exc)[:500]


async def ensure_llm_provider_seed(session: AsyncSession) -> None:
    """llm_provider 表为空时，用环境变量的 LLM 配置播种并置为启用，保证无缝迁移。"""
    result = await session.execute(
        select(sa_func.count()).select_from(LlmProviderMySQL)
    )
    if result.scalar_one() > 0:
        return
    env_config = app_config.llm
    if not env_config.api_key or env_config.api_key == "replace-me":
        logger.warning("llm_provider 表为空且未配置 LLM_API_KEY，跳过供应商播种")
        return
    provider = LlmProviderMySQL(
        id=str(uuid.uuid4()),
        name=f"{env_config.provider}（环境变量）",
        provider_type=env_config.provider,
        base_url=env_config.base_url,
        model_name=env_config.model_name,
        api_key_encrypted=encrypt_secret(env_config.api_key),
        temperature=env_config.temperature,
        timeout_seconds=env_config.timeout_seconds,
        is_active=True,
    )
    session.add(provider)
    await session.commit()
    logger.info("llm_provider 表为空，已从环境变量播种供应商 {}", provider.name)
