"""Tests for local vector rebuild and startup auto-trigger."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_maybe_rebuild_on_startup_triggers_when_local():
    from app.services.ai.local_vector_rebuild import maybe_rebuild_local_vectors_on_startup

    with patch("app.services.ai.local_vector_rebuild.settings.REDIS_ENABLE", True), \
         patch("app.services.config_service.ConfigService.get", return_value="local") as mock_get, \
         patch(
             "app.services.ai.local_vector_rebuild.rebuild_local_vector_indexes",
             new_callable=AsyncMock,
             return_value={"status": "success", "logs": ["ok"]},
         ) as mock_rebuild:
        await maybe_rebuild_local_vectors_on_startup()
        mock_get.assert_awaited()
        mock_rebuild.assert_awaited_once_with(
            drop_indexes=False,
            acquire_lock=True,
            trigger="startup",
        )


@pytest.mark.asyncio
async def test_maybe_rebuild_on_startup_skips_when_ragflow():
    from app.services.ai.local_vector_rebuild import maybe_rebuild_local_vectors_on_startup

    with patch("app.services.ai.local_vector_rebuild.settings.REDIS_ENABLE", True), \
         patch("app.services.config_service.ConfigService.get", return_value="ragflow"), \
         patch(
             "app.services.ai.local_vector_rebuild.rebuild_local_vector_indexes",
             new_callable=AsyncMock,
         ) as mock_rebuild:
        await maybe_rebuild_local_vectors_on_startup()
        mock_rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_skips_when_lock_held():
    from app.services.ai.local_vector_rebuild import rebuild_local_vector_indexes

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=False)

    with patch("app.services.ai.local_vector_rebuild.settings.REDIS_ENABLE", True), \
         patch("app.services.ai.local_vector_rebuild.redis.get_redis", return_value=mock_redis):
        res = await rebuild_local_vector_indexes(acquire_lock=True, trigger="startup")
        assert res["status"] == "skipped"
        mock_redis.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_startup_sync_does_not_drop_indexes():
    from app.services.ai.local_vector_rebuild import rebuild_local_vector_indexes

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.execute_command = AsyncMock()

    with patch("app.services.ai.local_vector_rebuild.settings.REDIS_ENABLE", True), \
         patch("app.services.ai.local_vector_rebuild.redis.get_redis", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True), \
         patch("app.services.ai.example_index_service.ExampleIndexService.ensure_index", return_value=True), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.sync_all_datasets", new_callable=AsyncMock), \
         patch("app.services.ai.example_index_service.ExampleIndexService.sync_all_examples", new_callable=AsyncMock):
        res = await rebuild_local_vector_indexes(
            drop_indexes=False,
            acquire_lock=True,
            trigger="startup",
        )
        assert res["status"] == "success"
        assert "同步" in res["message"]
        mock_redis.execute_command.assert_not_called()
