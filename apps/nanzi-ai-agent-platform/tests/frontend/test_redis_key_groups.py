"""Contract tests for Redis key grouping utility used in cleanup modal."""

from pathlib import Path


def test_redis_key_groups_module_exists():
    path = Path("frontend/src/utils/redisKeyGroups.ts")
    assert path.exists()


def test_redis_key_groups_supports_type_and_business_modes():
    content = Path("frontend/src/utils/redisKeyGroups.ts").read_text(encoding="utf-8")
    assert "groupRedisKeys" in content
    assert "'redis_type'" in content or '"redis_type"' in content
    assert "'business'" in content or '"business"' in content
    assert "conversation:" in content


def test_redis_cleanup_modal_wired_in_system_config():
    modal = Path("frontend/src/components/system/RedisKeyCleanupModal.vue").read_text(encoding="utf-8")
    content = Path("frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
    assert "RedisKeyCleanupModal" in content
    assert "清理 Keys" in content
    assert "/api/portal/system/redis/delete-keys" in modal
    assert "查看详情" in modal
    assert "/api/portal/system/redis/key-detail" in modal
