"""Embedding API client for memory service (OpenAI-compatible)."""
import logging
from typing import List, Optional

import httpx

from app.services.config_service import ConfigService
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)


class EmbeddingClient:
    @staticmethod
    async def _resolve_credentials(use_global: bool = False) -> tuple[str, str, str]:
        if use_global:
            base_url = (await ConfigService.get("embed_api_url") or "").strip()
            api_key = (await ConfigService.get("embed_api_key") or "").strip()
            model = (await ConfigService.get("embed_model_name") or "bge-m3").strip()

            # 降级逻辑：如果全局没有配置，则尝试向记忆配置或默认 LLM 配置兼容
            if not base_url:
                base_url = (await MemoryConfigService.get("memory_embedding_base_url") or "").strip()
            if not api_key:
                api_key = (await MemoryConfigService.get("memory_embedding_api_key") or "").strip()
            if not base_url:
                base_url = (await ConfigService.get("llm_base_url") or "").strip()
            if not api_key:
                api_key = (await ConfigService.get("llm_api_key") or "").strip()
            return base_url.rstrip("/"), api_key, model
        else:
            base_url = (await MemoryConfigService.get("memory_embedding_base_url") or "").strip()
            api_key = (await MemoryConfigService.get("memory_embedding_api_key") or "").strip()
            model = (await MemoryConfigService.get("memory_embedding_model") or "text-embedding-3-small").strip()

            if not base_url:
                base_url = (await ConfigService.get("llm_base_url") or "").strip()
            if not api_key:
                api_key = (await ConfigService.get("llm_api_key") or "").strip()
            return base_url.rstrip("/"), api_key, model

    @staticmethod
    async def embed_text(text: str, use_global: bool = False) -> List[float]:
        base_url, api_key, model = await EmbeddingClient._resolve_credentials(use_global)
        if not base_url or not api_key:
            err_msg = (
                "Embedding API 未配置：请在智能体设置中配置全局 Embedding URL 与 Key"
                if use_global
                else "Embedding API 未配置：请在记忆管理中心配置 URL 与 Key"
            )
            raise RuntimeError(err_msg)

        base = base_url.rstrip("/")
        if base.endswith("/embeddings"):
            url = base
        elif base.endswith("/v1"):
            url = f"{base}/embeddings"
        else:
            url = f"{base}/v1/embeddings"

        payload = {"model": model, "input": text}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data") or []
        if not items:
            raise RuntimeError("Embedding API 返回空 data")
        embedding = items[0].get("embedding")
        if not embedding:
            raise RuntimeError("Embedding API 返回无 embedding 字段")
        return [float(x) for x in embedding]

    @staticmethod
    async def get_dimensions(use_global: bool = False) -> int:
        if use_global:
            dim_str = await ConfigService.get("embed_dimensions")
            if dim_str and dim_str.isdigit():
                return int(dim_str)
            # 降级：如果全局没配置，获取记忆库维度
            mem_dim = await MemoryConfigService.get_int("memory_embedding_dimensions", 0)
            if mem_dim > 0:
                return mem_dim
            return 1024
        return await MemoryConfigService.get_int("memory_embedding_dimensions", 1536)
