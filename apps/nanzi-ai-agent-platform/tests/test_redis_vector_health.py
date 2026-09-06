"""Redis vector health check service tests."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.redis_vector_health import RedisVectorHealthService


@pytest.mark.asyncio
async def test_check_fails_when_redis_disabled():
    RedisVectorHealthService.invalidate_cache()
    with patch("app.services.ai.redis_vector_health.settings") as mock_settings:
        mock_settings.REDIS_ENABLE = False
        mock_settings.REDIS_HOST = "localhost"
        mock_settings.REDIS_PORT = 6379
        mock_settings.REDIS_DB = 0
        result = await RedisVectorHealthService.check(force=True)
    assert result["ok"] is False
    assert any(c["name"] == "redis_enabled" and not c["passed"] for c in result["checks"])


@pytest.mark.asyncio
async def test_check_fails_when_db_not_zero():
    RedisVectorHealthService.invalidate_cache()
    redis = AsyncMock()
    redis.ping = AsyncMock()
    redis.info = AsyncMock(return_value={"redis_version": "7.4"})
    redis.execute_command = AsyncMock(
        side_effect=lambda cmd, *args: (
            [["name", "search", "ver", 1]] if cmd == "MODULE" else None
        )
    )
    with patch("app.services.ai.redis_vector_health.settings") as mock_settings:
        mock_settings.REDIS_ENABLE = True
        mock_settings.REDIS_HOST = "h"
        mock_settings.REDIS_PORT = 6379
        mock_settings.REDIS_DB = 2
        with patch(
            "app.services.ai.redis_vector_health.get_redis",
            new_callable=AsyncMock,
            return_value=redis,
        ):
            result = await RedisVectorHealthService.check(force=True)
    assert result["ok"] is False
    db_check = next(c for c in result["checks"] if c["name"] == "redis_db")
    assert db_check["passed"] is False


@pytest.mark.asyncio
async def test_check_passes_when_vector_create_ok():
    RedisVectorHealthService.invalidate_cache()
    redis = AsyncMock()
    redis.ping = AsyncMock()
    redis.info = AsyncMock(return_value={"redis_version": "7.4"})

    async def cmd(command, *args):
        if command == "MODULE":
            return [["name", "search", "ver", 21020]]
        if command in ("FT.CREATE", "FT.DROPINDEX"):
            return "OK"
        return None

    redis.execute_command = AsyncMock(side_effect=cmd)
    with patch("app.services.ai.redis_vector_health.settings") as mock_settings:
        mock_settings.REDIS_ENABLE = True
        mock_settings.REDIS_HOST = "localhost"
        mock_settings.REDIS_PORT = 6379
        mock_settings.REDIS_DB = 0
        with patch(
            "app.services.ai.redis_vector_health.get_redis",
            new_callable=AsyncMock,
            return_value=redis,
        ):
            result = await RedisVectorHealthService.check(force=True)
    assert result["ok"] is True
    assert result["hints"] == []
