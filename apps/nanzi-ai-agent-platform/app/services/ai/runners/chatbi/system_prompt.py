"""ChatBI system prompt assembly."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.time_anchor import build_data_query_time_anchor_block


async def build_system_content(
    runner: Any,
    *,
    context_action_result: Optional[Dict[str, Any]] = None,
    include_context_action: bool = False,
) -> str:
    system_prompt = runner.config.system_prompt or ""
    if "{dataset_menu}" in system_prompt:
        user_id = runner.user_info.get("user_id") if runner.user_info else None
        is_admin = runner.user_info.get("role") == "admin" if runner.user_info else False
        dataset_menu = await AgentConfigProvider.get_dataset_menu(
            user_id=user_id,
            is_admin=is_admin,
        )
        system_prompt = system_prompt.replace("{dataset_menu}", dataset_menu)
    context_action_prompt = ""
    if include_context_action:
        result_json = ""
        if context_action_result:
            result_json = json.dumps(context_action_result, ensure_ascii=False)
            if len(result_json) > 20000:
                result_json = result_json[:20000] + "\n... [上一轮结果过长已截断]"
        context_action_prompt = f"\n\n{DataQueryPrompts.context_action_guide(result_json)}"
    time_anchor = build_data_query_time_anchor_block()
    sql_plan_block = (
        DataQueryPrompts.SQL_PLAN_ENFORCEMENT + "\n\n"
        if runner._is_sql_plan_enabled()
        else ""
    )
    return (
        f"{DataQueryPrompts.GLOBAL_GUARDRAILS}\n\n"
        f"{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}\n\n"
        f"{sql_plan_block}"
        f"{time_anchor}\n\n"
        f"{DataQueryPrompts.FOLLOWUP_REUSE_CONSTRAINT}\n\n"
        f"{system_prompt}{context_action_prompt}"
    )
