from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


SYSTEM_CONFIG = Path("frontend/src/views/SystemConfig.vue")


def test_system_diagnostics_exposes_redis_vector_search_card():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    assert "Redis 向量搜索" in source
    assert "/api/portal/memory/redis-vector-test" in source
    assert "testRedisVectorSearch" in source
    assert "force: true" in source
