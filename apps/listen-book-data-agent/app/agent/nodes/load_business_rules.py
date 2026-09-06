from __future__ import annotations

import time

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.log import logger


async def load_business_rules(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
):
    """Load only reviewed and published typed fragments in the current policy scope."""

    started_at = time.perf_counter()
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "加载业务规则", "status": "running"})
    service = runtime.context.get("business_rule_service")
    if service is None:
        writer({"type": "progress", "step": "加载业务规则", "status": "success"})
        return {"business_rules": [], "business_rule_matches": []}

    access_policy = state.get("access_policy", {})
    domain = str(access_policy.get("domain") or "audio")
    datasource = str(access_policy.get("datasource") or app_config.db_dw.database)
    intent = str(state.get("analysis_plan", {}).get("intent") or "detail")
    semantic_ids = _semantic_ids(state)
    try:
        resolver = getattr(service, "resolve_applicable", None)
        if resolver is None:
            rules = await service.list_applicable(
                domain=domain,
                datasource=datasource,
                intent=intent,
                semantic_ids=semantic_ids,
            )
            release_metadata = {}
        else:
            resolution = await resolver(
                domain=domain,
                datasource=datasource,
                intent=intent,
                semantic_ids=semantic_ids,
            )
            rules = resolution.rules
            release_metadata = {
                key: value
                for key, value in {
                    "semantic_release_id": resolution.semantic_release_id,
                    "semantic_release_version": resolution.semantic_release_version,
                    "business_rule_set_id": resolution.business_rule_set_id,
                    "business_rule_set_version": resolution.business_rule_set_version,
                }.items()
                if value is not None
            }
    except Exception as exc:
        logger.warning("业务规则召回降级：{}", exc)
        writer(
            {
                "type": "warning",
                "stage": "business_rule_retrieval",
                "message": "业务规则暂时不可用，已继续使用基础语义层。",
            }
        )
        writer(
            {
                "type": "progress",
                "step": "加载业务规则",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
            }
        )
        return {"business_rules": [], "business_rule_matches": []}

    prompt_rules = [
        {
            "rule_key": rule.rule_key,
            "version": rule.version,
            "rule_type": rule.rule_type,
            "content": rule.content,
            "priority": rule.priority,
        }
        for rule in rules
    ]
    public_matches = [
        {
            "rule_key": rule.rule_key,
            "version": rule.version,
            "rule_type": rule.rule_type,
        }
        for rule in rules
    ]
    writer(
        {
            "type": "context",
            "business_rule_matches": public_matches,
            **release_metadata,
        }
    )
    writer(
        {
            "type": "progress",
            "step": "加载业务规则",
            "status": "success",
            "duration_ms": _elapsed_ms(started_at),
        }
    )
    return {
        "business_rules": prompt_rules,
        "business_rule_matches": public_matches,
        **release_metadata,
    }


def _semantic_ids(state: DataAgentState) -> set[str]:
    values = {str(item.get("id")) for item in state.get("metric_infos", []) if item.get("id")}
    for table in state.get("table_infos", []):
        if table.get("id"):
            values.add(str(table["id"]))
        for column in table.get("columns", []):
            if column.get("id"):
                values.add(str(column["id"]))
    return values


def _elapsed_ms(started_at: float) -> int:
    return max(1, round((time.perf_counter() - started_at) * 1000))
