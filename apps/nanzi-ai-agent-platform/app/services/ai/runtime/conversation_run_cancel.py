from __future__ import annotations

import logging
from typing import Any

from app.services.ai.runtime.agentscope.session_lock import agentscope_session_lock
from app.services.ai.runtime.session_run_lane import conversation_run_lane

logger = logging.getLogger(__name__)


async def release_conversation_run_locks(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Force-release per-conversation run lane and AgentScope session locks.

    Used when the client aborts SSE or explicitly cancels generation so the
    user can send a new message without waiting for Redis lock TTL expiry.
    """
    if not conversation_id:
        return {
            "success": False,
            "lane_released": False,
            "session_locks_released": 0,
        }

    lane_released = await conversation_run_lane.force_release(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    session_locks_released = await agentscope_session_lock.force_release_all_for_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
    )

    if lane_released or session_locks_released:
        logger.info(
            "[ConversationRunCancel] released locks conversation=%s user=%s trace=%s "
            "lane=%s session_locks=%s",
            conversation_id,
            user_id,
            trace_id,
            lane_released,
            session_locks_released,
        )

    return {
        "success": lane_released or session_locks_released > 0,
        "lane_released": lane_released,
        "session_locks_released": session_locks_released,
    }
