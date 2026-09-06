"""Manual-first, permission-filtered follow-up question recommendations."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.llm import get_llm
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt
from app.services.access_policy import AccessPolicyContextV1

_DEFAULT_CONFIG = Path(__file__).parents[2] / "conf" / "domains" / "audio" / "recommendations.yaml"
_BLOCKED_TERMS = (
    "手机号",
    "手机号码",
    "邮箱",
    "身份证",
    "密码",
    "密钥",
    "api key",
    "提示词",
    "系统提示",
    "逐行用户",
)
_TABLE_HINTS = {
    "会员": {"member_account"},
    "订单": {"content_order"},
    "支付": {"payment_record"},
    "退款": {"refund_record"},
    "搜索": {"search_query_log", "search_keyword_stat"},
    "榜单": {"ranking_list", "ranking_item"},
    "播放": {"play_session"},
}
_SAFE_FILLERS = (
    "把时间范围改为本月",
    "按天查看同一指标趋势",
    "查看同一指标的前 10 名",
)


@dataclass(frozen=True)
class RecommendationResult:
    questions: tuple[str, ...]
    source: str
    llm_calls: int


class RecommendationService:
    def __init__(
        self,
        *,
        manual_recommendations: list[dict] | None = None,
        llm_factory: Callable[[], Any] = get_llm,
    ):
        self.manual_recommendations = (
            manual_recommendations
            if manual_recommendations is not None
            else _load_manual_recommendations(_DEFAULT_CONFIG)
        )
        self.llm_factory = llm_factory

    async def recommend(
        self,
        *,
        question: str,
        query_plan: dict,
        answer_summary: str,
        current_tables: list[str],
        access_policy: AccessPolicyContextV1,
        limit: int = 3,
        usage_callback: Any | None = None,
    ) -> RecommendationResult:
        intent = str(query_plan.get("intent") or "aggregate")
        candidates: list[tuple[str, tuple[str, ...]]] = []
        for item in sorted(
            self.manual_recommendations,
            key=lambda value: int(value.get("priority", 0)),
            reverse=True,
        ):
            intents = {str(value) for value in item.get("intents", [])}
            if intents and intent not in intents:
                continue
            required = tuple(str(value) for value in item.get("required_tables", []))
            candidates.append((str(item.get("question") or ""), required))

        accepted = _filter_candidates(
            candidates,
            current_tables=current_tables,
            access_policy=access_policy,
            limit=limit,
        )
        llm_calls = 0
        if len(accepted) < limit:
            llm_calls = 1
            try:
                ai_candidates = await self._generate_ai_candidates(
                    question=question,
                    query_plan=query_plan,
                    answer_summary=answer_summary,
                    current_tables=current_tables,
                    access_policy=access_policy,
                    usage_callback=usage_callback,
                )
                accepted = _filter_candidates(
                    [*[(value, ()) for value in accepted], *ai_candidates],
                    current_tables=current_tables,
                    access_policy=access_policy,
                    limit=limit,
                )
            except Exception:
                logger.warning("AI 追问补位失败，使用安全确定性建议补齐")

        if len(accepted) < limit:
            accepted = _filter_candidates(
                [*[(value, ()) for value in accepted], *[(value, ()) for value in _SAFE_FILLERS]],
                current_tables=current_tables,
                access_policy=access_policy,
                limit=limit,
            )
        return RecommendationResult(
            questions=tuple(accepted[:limit]),
            source="hybrid" if llm_calls else "manual",
            llm_calls=llm_calls,
        )

    async def _generate_ai_candidates(
        self,
        *,
        question: str,
        query_plan: dict,
        answer_summary: str,
        current_tables: list[str],
        access_policy: AccessPolicyContextV1,
        usage_callback: Any | None,
    ) -> list[tuple[str, tuple[str, ...]]]:
        allowed_tables = _effective_allowed_tables(access_policy, current_tables)
        prompt = load_prompt("recommend_questions").format(
            question=question[:500],
            query_plan=json.dumps(query_plan, ensure_ascii=False),
            answer_summary=answer_summary[:1000],
            allowed_tables=json.dumps(sorted(allowed_tables), ensure_ascii=False),
        )
        llm = self.llm_factory()
        if inspect.isawaitable(llm):
            llm = await llm
        messages = [
            SystemMessage(content="严格遵守 JSON 输出和数据权限约束。"),
            HumanMessage(content=prompt),
        ]
        response = (
            await llm.ainvoke(messages, config={"callbacks": [usage_callback]})
            if usage_callback is not None
            else await llm.ainvoke(messages)
        )
        content = response.content if hasattr(response, "content") else response
        return _parse_ai_candidates(str(content))


def _load_manual_recommendations(path: Path) -> list[dict]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("人工追问配置加载失败：{}", path)
        return []
    recommendations = payload.get("recommendations", [])
    return [item for item in recommendations if isinstance(item, dict)]


def _parse_ai_candidates(raw: str) -> list[tuple[str, tuple[str, ...]]]:
    value = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    payload = json.loads(value)
    if not isinstance(payload, list):
        raise ValueError("AI 追问输出必须是数组")
    candidates: list[tuple[str, tuple[str, ...]]] = []
    for item in payload[:6]:
        if not isinstance(item, dict):
            continue
        required = item.get("required_tables", [])
        if not isinstance(required, list):
            continue
        candidates.append(
            (
                str(item.get("question") or ""),
                tuple(str(table) for table in required),
            )
        )
    return candidates


def _filter_candidates(
    candidates: list[tuple[str, tuple[str, ...]]],
    *,
    current_tables: list[str],
    access_policy: AccessPolicyContextV1,
    limit: int,
) -> list[str]:
    accepted: list[str] = []
    signatures: set[str] = set()
    for raw_question, declared_tables in candidates:
        question = re.sub(r"\s+", " ", raw_question).strip(" ，,。？?")
        signature = re.sub(r"[\s，,。？?]", "", question).lower()
        if not question or len(question) > 100 or signature in signatures:
            continue
        required_tables = set(declared_tables) or set(current_tables)
        required_tables.update(_inferred_tables(question))
        if _blocked_question(question) or not _tables_allowed(required_tables, access_policy):
            continue
        accepted.append(question)
        signatures.add(signature)
        if len(accepted) >= limit:
            break
    return accepted


def _blocked_question(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in _BLOCKED_TERMS) or bool(
        re.search(r"(?i)\b(?:select|insert|update|delete|drop|alter)\b", question)
    )


def _inferred_tables(question: str) -> set[str]:
    inferred: set[str] = set()
    for hint, tables in _TABLE_HINTS.items():
        if hint in question:
            inferred.update(tables)
    return inferred


def _tables_allowed(tables: set[str], access_policy: AccessPolicyContextV1) -> bool:
    if not tables or access_policy.admin_bypass or "*" in access_policy.table_acl:
        return True
    return tables.issubset(access_policy.table_acl)


def _effective_allowed_tables(
    access_policy: AccessPolicyContextV1,
    current_tables: list[str],
) -> set[str]:
    if access_policy.admin_bypass or "*" in access_policy.table_acl:
        return set(current_tables)
    return set(access_policy.table_acl)
