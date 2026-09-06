"""Resolve bounded conversation history into a standalone analytics question."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.analysis_plan import build_analysis_plan
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.sql_template_service import build_parameterized_sql_template

_NO_INHERIT_PATTERNS = (
    r"不要继承(?:之前|上文|历史)?(?:的)?(?:条件|内容|上下文)?[，,：:]?",
    r"不要参考(?:之前|上文|历史)(?:的)?(?:条件|内容|上下文)?[，,：:]?",
    r"(?:重新开始|独立查询)[，,：:]?",
)
_FOLLOW_UP_MARKERS = (
    "那",
    "那么",
    "然后",
    "再看",
    "改成",
    "改为",
    "换成",
    "只看",
    "仅看",
    "拆开",
    "拆分",
    "还有",
    "这个",
    "那个",
    "换一个",
    "呢",
)
_TIME_PATTERN = re.compile(
    r"(?:最近|近)\s*[一二两三四五六七八九十\d]+\s*(?:个)?(?:天|日|月)|"
    r"上个月|今天|昨天|本周|上周|本月|上月|本季度|今年|本年"
)
_GRAIN_PATTERN = re.compile(
    r"按(?:小时|天|日|周|月)(?:粒度)?|"
    r"每(?:小时|天|日|周|月)|"
    r"(?:小时|日|周|月)趋势|每日|月度|(?:小时|日|周|月)粒度"
)
_EXPLICIT_GRAIN = re.compile(r"(?:按|每|改成|改为|换成)?(小时|天|日|周|月)(?:粒度|趋势)?")


@dataclass(frozen=True)
class ConversationTurnContext:
    trace_id: str
    standalone_question: str
    query_plan: dict
    verified_sql_template: str | None
    answer_summary: str | None


@dataclass(frozen=True)
class ContextResolution:
    standalone_question: str
    inherited: bool
    used_trace_ids: tuple[str, ...]
    confidence: str
    ambiguity_reason: str | None
    turns: tuple[ConversationTurnContext, ...]


async def resolve_conversation_context(
    *,
    query: str,
    conversation_id: str | None,
    parent_trace_id: str | None,
    regenerate_of_trace_id: str | None,
    user_id: str | None,
    repository: QueryTraceRepository,
    row_level_scope: list[dict] | None,
    dialect: str,
) -> ContextResolution:
    """Load only owned successful ancestors and resolve before intent/RAG starts."""

    if conversation_id is None or user_id is None:
        return _standalone(query)

    current_query = query
    if regenerate_of_trace_id is not None:
        source = await repository.get_for_user(regenerate_of_trace_id, user_id)
        if source is not None and source.conversation_id == conversation_id:
            current_query = source.standalone_question or source.query_text

    ancestors = await repository.list_successful_ancestors_for_user(
        conversation_id=conversation_id,
        parent_trace_id=parent_trace_id,
        user_id=user_id,
        limit=3,
    )
    turns = tuple(
        _to_turn_context(
            trace,
            row_level_scope=row_level_scope,
            dialect=dialect,
        )
        for trace in ancestors
    )
    return resolve_standalone_question(current_query, turns)


def resolve_standalone_question(
    query: str,
    turns: tuple[ConversationTurnContext, ...],
) -> ContextResolution:
    """Apply explicit reset, current modifiers, then bounded history in that order."""

    reset_query, reset = _strip_no_inherit(query)
    if reset:
        if reset_query:
            return _standalone(reset_query, turns=turns)
        return ContextResolution(
            standalone_question=query.strip(),
            inherited=False,
            used_trace_ids=(),
            confidence="low",
            ambiguity_reason="已清除历史上下文，但当前轮没有新的查询条件",
            turns=turns,
        )
    if not _looks_like_follow_up(query):
        return _standalone(query, turns=turns)
    if not turns:
        return ContextResolution(
            standalone_question=query.strip(),
            inherited=False,
            used_trace_ids=(),
            confidence="low",
            ambiguity_reason="缺少可用的成功祖先轮次",
            turns=(),
        )

    modifier = _normalize_modifier(query)
    if not modifier:
        return ContextResolution(
            standalone_question=query.strip(),
            inherited=False,
            used_trace_ids=(),
            confidence="low",
            ambiguity_reason="当前轮没有明确的指标、筛选、时间或粒度条件",
            turns=turns,
        )

    previous = next((turn for turn in turns if turn.standalone_question.strip()), turns[0])
    base = previous.standalone_question.strip()
    current_plan = build_analysis_plan(modifier).to_state()
    if not _has_resolvable_modifier(modifier, current_plan):
        return ContextResolution(
            standalone_question=query.strip(),
            inherited=False,
            used_trace_ids=(),
            confidence="low",
            ambiguity_reason="当前轮没有明确的指标、筛选、时间或粒度条件",
            turns=turns,
        )
    previous_plan = previous.query_plan or build_analysis_plan(base).to_state()

    if current_plan.get("time_range", {}).get("label") or _TIME_PATTERN.search(modifier):
        base = _TIME_PATTERN.sub("", base)
    if _explicit_grain(modifier):
        base = _GRAIN_PATTERN.sub("", base)
        modifier = _canonical_grain_modifier(modifier)
    if current_plan.get("metric_hints"):
        base = _remove_previous_metrics(base, previous_plan)
    region = _only_region(modifier)
    if region:
        base = _remove_previous_region(base, previous_plan)
        modifier = f"{region}地区"
    modifier = re.sub(r"拆开|拆分看看|展开看看", "拆分", modifier)
    standalone = _join_question(base, modifier)
    return ContextResolution(
        standalone_question=standalone,
        inherited=True,
        used_trace_ids=tuple(turn.trace_id for turn in turns),
        confidence="high",
        ambiguity_reason=None,
        turns=turns,
    )


def _to_turn_context(trace, *, row_level_scope: list[dict] | None, dialect: str):
    sql_template: str | None = None
    if trace.sql:
        template = build_parameterized_sql_template(
            trace.sql,
            row_level_scope=row_level_scope,
            dialect=dialect,
        )
        if template.sql and not template.sql.startswith("/* redacted:"):
            sql_template = template.sql
    return ConversationTurnContext(
        trace_id=trace.id,
        standalone_question=trace.standalone_question or trace.query_text,
        query_plan=trace.query_plan_summary or {},
        verified_sql_template=sql_template,
        answer_summary=trace.answer_summary,
    )


def _standalone(
    query: str,
    *,
    turns: tuple[ConversationTurnContext, ...] = (),
) -> ContextResolution:
    return ContextResolution(
        standalone_question=query.strip(),
        inherited=False,
        used_trace_ids=(),
        confidence="high",
        ambiguity_reason=None,
        turns=turns,
    )


def _strip_no_inherit(query: str) -> tuple[str, bool]:
    value = query.strip()
    reset = False
    for pattern in _NO_INHERIT_PATTERNS:
        updated, count = re.subn(pattern, "", value, count=1)
        if count:
            reset = True
            value = updated.strip(" ，,。")
            break
    return value, reset


def _looks_like_follow_up(query: str) -> bool:
    value = query.strip(" ，,。？?")
    if any(marker in value for marker in _FOLLOW_UP_MARKERS):
        return True
    plan = build_analysis_plan(value).to_state()
    return len(value) <= 24 and not plan.get("metric_hints") and (
        bool(plan.get("time_range", {}).get("label"))
        or bool(plan.get("dimensions"))
        or bool(_explicit_grain(value))
    )


def _normalize_modifier(query: str) -> str:
    value = query.strip(" ，,。？?")
    value = re.sub(r"^(?:那|那么|然后|再看|再|接着)", "", value)
    value = re.sub(r"(?:可以吗|怎么样|如何|呢)$", "", value)
    return value.strip(" ，,。？?")


def _has_resolvable_modifier(modifier: str, plan: dict) -> bool:
    return bool(
        _TIME_PATTERN.search(modifier)
        or _explicit_grain(modifier)
        or _only_region(modifier)
        or plan.get("metric_hints")
        or plan.get("dimensions")
        or plan.get("filter_requirements")
        or plan.get("filters")
        or plan.get("comparison")
        or plan.get("top_n")
        or any(word in modifier for word in ("明细", "排行", "排名", "趋势", "对比"))
    )


def _explicit_grain(query: str) -> str | None:
    match = _EXPLICIT_GRAIN.search(query)
    if match is None:
        return None
    grain = match.group(1)
    if f"{grain}粒度" in query or any(
        marker in query for marker in (f"按{grain}", f"每{grain}", "改成", "改为", "换成")
    ):
        return grain
    return None


def _canonical_grain_modifier(query: str) -> str:
    grain = _explicit_grain(query)
    return f"按{'天' if grain == '日' else grain}" if grain else query


def _only_region(query: str) -> str | None:
    value = query.strip(" ，,。")
    for prefix in ("只看", "仅看"):
        if value.startswith(prefix):
            region = value[len(prefix) :]
            for suffix in ("地区", "区域"):
                if region.endswith(suffix):
                    region = region[: -len(suffix)]
            return region.strip() or None
    return None


def _remove_previous_metrics(base: str, plan: dict) -> str:
    hints = sorted(
        {str(item) for item in plan.get("metric_hints", []) if str(item)},
        key=len,
        reverse=True,
    )
    for hint in hints:
        base = base.replace(hint, "")
    return base


def _remove_previous_region(base: str, plan: dict) -> str:
    for requirement in plan.get("filter_requirements", []):
        columns = {str(item) for item in requirement.get("columns", [])}
        if not columns.intersection({"user_profile.province", "user_profile.city"}):
            continue
        for value in requirement.get("values", []):
            for candidate in (f"{value}地区", f"{value}区域", str(value)):
                base = base.replace(candidate, "")
    return base


def _join_question(base: str, modifier: str) -> str:
    cleaned_base = re.sub(r"[，,]{2,}", "，", base).strip(" ，,。")
    cleaned_modifier = modifier.strip(" ，,。")
    if not cleaned_base:
        return cleaned_modifier
    if not cleaned_modifier or cleaned_modifier in cleaned_base:
        return cleaned_base
    return f"{cleaned_base}，{cleaned_modifier}"
