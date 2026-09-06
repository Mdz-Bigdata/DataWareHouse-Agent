import pytest

from app.services.ai.runtime.agentscope.session_lock import (
    AgentScopeSessionLock,
    SessionLockTimeout,
)

pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
            return 1
        return 0

    async def scan_iter(self, match=None, count=50):
        prefix = (match or "").replace("*", "")
        for key in list(self.store.keys()):
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_session_lock_force_release_all_for_conversation(monkeypatch):
    fake = FakeRedis()
    lock = AgentScopeSessionLock()
    fake.store["conversation:u1:conv-3:agent_lock:DataAgent"] = "token-a"
    fake.store["conversation:u1:conv-3:agent_lock:GeneralAgent"] = "token-b"
    fake.store["conversation:u1:conv-other:agent_lock:DataAgent"] = "token-c"

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    released = await lock.force_release_all_for_conversation(
        user_id="u1",
        conversation_id="conv-3",
    )
    assert released == 2
    assert "conversation:u1:conv-3:agent_lock:DataAgent" not in fake.store
    assert "conversation:u1:conv-3:agent_lock:GeneralAgent" not in fake.store
    assert fake.store["conversation:u1:conv-other:agent_lock:DataAgent"] == "token-c"


@pytest.mark.asyncio
async def test_session_lock_acquire_and_release(monkeypatch):
    fake = FakeRedis()

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    lock = AgentScopeSessionLock()
    handle = await lock.acquire(
        user_id="u1",
        conversation_id="conv-1",
        agent_name="GeneralAgent",
        wait_seconds=1,
    )
    assert handle is not None
    key, token = handle
    assert fake.store[key] == token

    second = await lock.acquire(
        user_id="u1",
        conversation_id="conv-1",
        agent_name="GeneralAgent",
        wait_seconds=0.2,
    )
    assert second is None

    await lock.release(key, token)
    assert key not in fake.store

    third = await lock.acquire(
        user_id="u1",
        conversation_id="conv-1",
        agent_name="GeneralAgent",
        wait_seconds=0.2,
    )
    assert third is not None


@pytest.mark.asyncio
async def test_session_lock_hold_raises_on_timeout(monkeypatch):
    fake = FakeRedis()
    await fake.set("conversation:u1:conv-2:agent_lock:DataAgent", "occupied", nx=True)

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    lock = AgentScopeSessionLock()
    with pytest.raises(SessionLockTimeout):
        async with lock.hold(
            user_id="u1",
            conversation_id="conv-2",
            agent_name="DataAgent",
            wait_seconds=0.2,
        ):
            pass


@pytest.mark.asyncio
async def test_session_lock_skips_without_conversation_id():
    lock = AgentScopeSessionLock()
    async with lock.hold(
        user_id="u1",
        conversation_id=None,
        agent_name="GeneralAgent",
    ) as acquired:
        assert acquired is False
