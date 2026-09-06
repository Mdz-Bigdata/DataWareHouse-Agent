from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import get_meta_session
from app.api.deps import require_admin
from app.api.schemas.llm_provider_schema import (
    LlmProviderItem,
    LlmProviderTestRequest,
    LlmProviderTestResult,
    LlmProviderUpsert,
)
from app.core.crypto import mask_secret
from app.models.mysql.llm_provider_mysql import LlmProviderMySQL
from app.models.mysql.user_mysql import UserMySQL
from app.services.llm_provider_service import LlmProviderService

admin_llm_router = APIRouter(tags=["LLM 供应商管理"])


def _to_item(service: LlmProviderService, provider: LlmProviderMySQL) -> LlmProviderItem:
    return LlmProviderItem(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        model_name=provider.model_name,
        api_key_masked=mask_secret(service.decrypt_api_key(provider)),
        temperature=provider.temperature,
        timeout_seconds=provider.timeout_seconds,
        is_active=provider.is_active,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


async def _get_or_404(
    service: LlmProviderService, provider_id: str
) -> LlmProviderMySQL:
    provider = await service.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="供应商不存在")
    return provider


@admin_llm_router.get("/api/admin/llm-providers", response_model=list[LlmProviderItem])
async def list_providers(
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = LlmProviderService(meta_session)
    providers = await service.list_all()
    return [_to_item(service, provider) for provider in providers]


@admin_llm_router.post(
    "/api/admin/llm-providers",
    response_model=LlmProviderItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider(
    body: LlmProviderUpsert,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = LlmProviderService(meta_session)
    try:
        provider = await service.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _to_item(service, provider)


@admin_llm_router.put(
    "/api/admin/llm-providers/{provider_id}", response_model=LlmProviderItem
)
async def update_provider(
    provider_id: str,
    body: LlmProviderUpsert,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = LlmProviderService(meta_session)
    provider = await _get_or_404(service, provider_id)
    await service.update(provider, body)
    return _to_item(service, provider)


@admin_llm_router.delete("/api/admin/llm-providers/{provider_id}")
async def delete_provider(
    provider_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = LlmProviderService(meta_session)
    provider = await _get_or_404(service, provider_id)
    try:
        await service.delete(provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"status": "ok"}


@admin_llm_router.post(
    "/api/admin/llm-providers/{provider_id}/activate", response_model=LlmProviderItem
)
async def activate_provider(
    provider_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
):
    service = LlmProviderService(meta_session)
    provider = await _get_or_404(service, provider_id)
    await service.activate(provider)
    return _to_item(service, provider)


@admin_llm_router.post(
    "/api/admin/llm-providers/{provider_id}/test", response_model=LlmProviderTestResult
)
async def test_provider(
    provider_id: str,
    _: Annotated[UserMySQL, Depends(require_admin)],
    meta_session: Annotated[AsyncSession, Depends(get_meta_session)],
    body: LlmProviderTestRequest | None = None,
):
    """测试已保存的供应商；body 传草稿配置时按草稿测试，api_key 留空复用已存密钥。"""
    service = LlmProviderService(meta_session)
    provider = await _get_or_404(service, provider_id)
    stored_key = service.decrypt_api_key(provider)
    if body is None:
        config = {
            "provider_type": provider.provider_type,
            "base_url": provider.base_url,
            "model_name": provider.model_name,
            "api_key": stored_key,
            "temperature": provider.temperature,
            "timeout_seconds": provider.timeout_seconds,
        }
    else:
        config = {
            "provider_type": body.provider_type,
            "base_url": body.base_url,
            "model_name": body.model_name,
            "api_key": body.api_key or stored_key,
            "temperature": body.temperature,
            "timeout_seconds": body.timeout_seconds,
        }
    ok, latency_ms, error = await LlmProviderService.test_connection(**config)
    return LlmProviderTestResult(ok=ok, latency_ms=latency_ms, error=error)
