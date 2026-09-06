import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock
from typing import List, Dict

# Mock get_redis to avoid needing a real Redis connection for unit logic test
mock_redis_client = AsyncMock()

async def mock_get_redis():
    return mock_redis_client

# Patch the import in memory_service
import app.services.ai.memory_service
app.services.ai.memory_service.get_redis = mock_get_redis

from app.services.ai.memory_service import MemoryService

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_pagination_logic():
    print("🚀 Starting MemoryService Pagination Logic Test...")
    
    service = MemoryService(max_history_turns=50) # Max 100 messages
    
    # 1. Simulate 50 messages in Redis
    # Redis stores them as strings of JSON
    fake_messages = [f'{{"id": {i}, "content": "msg_{i}"}}' for i in range(50)]
    
    # Mock lrange to return our fake data
    # Note: In real Redis, lrange(0, -1) returns ALL items.
    mock_redis_client.lrange.return_value = fake_messages
    
    # --- Case 1: First Page (Latest 20) ---
    # offset=0, limit=20 -> Should get msg_30 to msg_49
    history = await service.get_history("u1", "c1", limit=20, offset=0)
    print(f"Case 1 (Offset 0): Got {len(history)} items.")
    assert len(history) == 20
    assert history[0]['content'] == "msg_30"
    assert history[-1]['content'] == "msg_49"
    print("✅ Case 1 Passed: Retrieved latest 20 items correctly.")

    # --- Case 2: Second Page (Previous 20) ---
    # offset=20, limit=20 -> Should get msg_10 to msg_29
    history = await service.get_history("u1", "c1", limit=20, offset=20)
    print(f"Case 2 (Offset 20): Got {len(history)} items.")
    assert len(history) == 20
    assert history[0]['content'] == "msg_10"
    assert history[-1]['content'] == "msg_29"
    print("✅ Case 2 Passed: Retrieved previous 20 items correctly.")

    # --- Case 3: Last Page (Remaining 10) ---
    # offset=40, limit=20 -> Should get msg_0 to msg_9
    history = await service.get_history("u1", "c1", limit=20, offset=40)
    print(f"Case 3 (Offset 40): Got {len(history)} items.")
    assert len(history) == 10
    assert history[0]['content'] == "msg_0"
    assert history[-1]['content'] == "msg_9"
    print("✅ Case 3 Passed: Retrieved remaining 10 items correctly.")

    # --- Case 4: Out of Bounds ---
    # offset=60 -> Should get empty
    history = await service.get_history("u1", "c1", limit=20, offset=60)
    print(f"Case 4 (Offset 60): Got {len(history)} items.")
    assert len(history) == 0
    print("✅ Case 4 Passed: Handled out-of-bounds correctly.")
    
    print("\n🎉 All Logic Tests Passed!")

if __name__ == "__main__":
    asyncio.run(test_pagination_logic())
