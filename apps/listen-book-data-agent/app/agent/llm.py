"""LLM 客户端解析：数据库启用配置优先，环境变量兜底；按配置指纹缓存以支持热切换。"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from sqlalchemy import select

from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.conf.app_config import app_config
from app.core.crypto import decrypt_secret
from app.core.log import logger
from app.models.mysql.llm_provider_mysql import LlmProviderMySQL

# 网页配置里的供应商类型：openai_compatible 走 openai 客户端 + 自定义 base_url
_LANGCHAIN_PROVIDER = {
    "deepseek": "deepseek",
    "openai": "openai",
    "openai_compatible": "openai",
}

PROVIDER_TYPES = tuple(_LANGCHAIN_PROVIDER.keys())


def build_chat_model(
    *,
    provider_type: str,
    model_name: str,
    api_key: str,
    base_url: str,
    temperature: float,
    timeout_seconds: int,
) -> BaseChatModel:
    """按供应商配置构建聊天模型客户端（连接测试与热切换共用）。"""
    if provider_type not in _LANGCHAIN_PROVIDER:
        raise ValueError(f"不支持的供应商类型：{provider_type}")
    return init_chat_model(
        model=model_name,
        model_provider=_LANGCHAIN_PROVIDER[provider_type],
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        timeout=timeout_seconds,
    )


_llm_cache: dict[str, BaseChatModel] = {}


async def _load_active_provider() -> LlmProviderMySQL | None:
    """读取当前启用的供应商；表为空或读取失败时返回 None（回退环境变量）。"""
    if meta_mysql_client_manager.session_factory is None:
        return None
    try:
        async with meta_mysql_client_manager.session_factory() as session:
            result = await session.execute(
                select(LlmProviderMySQL).where(LlmProviderMySQL.is_active.is_(True))
            )
            return result.scalar_one_or_none()
    except Exception:
        logger.warning("读取启用中的 LLM 供应商失败，回退到环境变量配置")
        return None


async def get_llm() -> BaseChatModel:
    """按当前生效配置返回聊天模型客户端。

    配置指纹（供应商 id + 更新时间 / env）变化时重建客户端，
    因此在管理页切换启用供应商后下一次查询即生效，无需重启。
    """
    provider = await _load_active_provider()
    if provider is None:
        env_config = app_config.llm
        source = "env"
        params = {
            "provider_type": env_config.provider,
            "model_name": env_config.model_name,
            "api_key": env_config.api_key,
            "base_url": env_config.base_url,
            "temperature": env_config.temperature,
            "timeout_seconds": env_config.timeout_seconds,
        }
    else:
        source = provider.id
        params = {
            "provider_type": provider.provider_type,
            "model_name": provider.model_name,
            "api_key": decrypt_secret(provider.api_key_encrypted),
            "base_url": provider.base_url,
            "temperature": provider.temperature,
            "timeout_seconds": provider.timeout_seconds,
        }
    # 以完整配置为指纹：任何字段变化都重建客户端（updated_at 只有秒级精度，不可靠）
    cache_key = source + ":" + "|".join(str(params[key]) for key in sorted(params))
    if cache_key not in _llm_cache:
        _llm_cache.clear()
        _llm_cache[cache_key] = build_chat_model(**params)
    return _llm_cache[cache_key]
