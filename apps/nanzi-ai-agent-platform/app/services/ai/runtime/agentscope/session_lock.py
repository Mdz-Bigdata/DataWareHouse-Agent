from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL_SECONDS = 120
DEFAULT_WAIT_SECONDS = 15
DEFAULT_POLL_INTERVAL_SECONDS = 0.1


class SessionLockTimeout(RuntimeError):
    """Raised when a session lock cannot be acquired within the wait window."""


class AgentScopeSessionLock:
    """Redis distributed lock for per-session AgentState and resume operations."""

    def _lock_key(
        self,
        user_id: str | int | None,
        conversation_id: str,
        agent_name: str,
    ) -> str:
        from app.services.ai.memory_service import memory_service

        uid = str(user_id) if user_id is not None else "anonymous"
        safe_agent = agent_name.replace(":", "_")
        return (
            f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:"
            f"agent_lock:{safe_agent}"
        )

    async def acquire(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
        agent_name: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ) -> tuple[str, str] | None:
        if not conversation_id:
            return None

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return None

        key = self._lock_key(user_id, conversation_id, agent_name)
        token = uuid.uuid4().hex
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                acquired = await redis.set(key, token, ex=ttl_seconds, nx=True)
            except Exception as exc:
                logger.warning("[AgentScopeSessionLock] acquire failed: %s", exc)
                return None
            if acquired:
                return key, token
            await asyncio.sleep(DEFAULT_POLL_INTERVAL_SECONDS)

        logger.warning(
            "[AgentScopeSessionLock] timeout waiting for lock key=%s agent=%s",
            key,
            agent_name,
        )
        return None

    async def release(self, key: str | None, token: str | None) -> None:
        if not key or not token:
            return

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return

        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        try:
            await redis.eval(script, 1, key, token)
        except Exception as exc:
            logger.warning("[AgentScopeSessionLock] release failed: %s", exc)

    def _agent_lock_pattern(
        self,
        user_id: str | int | None,
        conversation_id: str,
    ) -> str:
        from app.services.ai.memory_service import memory_service

        uid = str(user_id) if user_id is not None else "anonymous"
        return f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:agent_lock:*"

    async def force_release_all_for_conversation(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> int:
        """Delete all AgentScope session locks for a conversation (client cancel)."""
        if not conversation_id:
            return 0

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return 0

        pattern = self._agent_lock_pattern(user_id, conversation_id)
        released = 0
        try:
            async for key in redis.scan_iter(match=pattern, count=50):
                deleted = await redis.delete(key)
                released += int(deleted or 0)
        except Exception as exc:
            logger.warning(
                "[AgentScopeSessionLock] force_release_all_for_conversation failed: %s",
                exc,
            )
        return released

    @asynccontextmanager
    async def hold(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
        agent_name: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ) -> AsyncIterator[bool]:
        if not conversation_id:
            yield False
            return

        handle = await self.acquire(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
            ttl_seconds=ttl_seconds,
            wait_seconds=wait_seconds,
        )
        if handle is None:
            from app.core.redis import get_redis

            if await get_redis() is None:
                yield False
                return
            raise SessionLockTimeout(
                f"Failed to acquire AgentScope session lock for conversation={conversation_id}"
            )
        key, token = handle
        try:
            yield True
        finally:
            await self.release(key, token)


agentscope_session_lock = AgentScopeSessionLock()
