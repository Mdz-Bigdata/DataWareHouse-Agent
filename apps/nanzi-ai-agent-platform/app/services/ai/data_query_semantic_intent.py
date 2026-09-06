"""Structured ChatBI query intent helpers.

This module keeps the semantic intent layer independent from the runner so it
can fail open: if LLM analysis or JSON parsing fails, existing keyword-only
schema search still works.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SemanticIntentFilter:
    phrase: str
    semantic_type: str = ""
    expected_column_types: list[str] = field(default_factory=list)
    avoid_column_types: list[str] = field(default_factory=list)
    relation: str = ""


@dataclass
class DataQuerySemanticIntent:
    goal: str = ""
    keywords: str = ""
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[SemanticIntentFilter] = field(default_factory=list)
    time_range: str = ""
    grain: str = ""
    target_datasets: list[str] = field(default_factory=list)
    reasoning: str = ""

    def has_content(self) -> bool:
        return bool(
            self.goal
            or self.keywords
            or self.metrics
            or self.dimensions
            or self.filters
            or self.time_range
            or self.grain
            or self.target_datasets
        )


def _as_clean_str(value: Any, *, max_len: int = 80) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _as_str_list(value: Any, *, max_items: int = 8, max_len: int = 40) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,，、\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _as_clean_str(raw, max_len=max_len)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


_LIST_QUERY_HINT_RE = re.compile(r"(?:列表|清单|明细|列出|展示|显示)")


def _is_full_scope_phrase(text: str) -> bool:
    cleaned = _as_clean_str(text)
    # 仅移除纯度极高、没有任何过滤属性的噪音词，防止将“所有北京的”等过滤词错误移去
    return cleaned in {"所有", "全部", "全量", "所有的", "全部的", "全量的"}


def _is_full_scope_list_query(question: str) -> bool:
    text = _as_clean_str(question, max_len=240)
    return bool(re.search(r"(?:所有|全部|全量)", text)) and bool(_LIST_QUERY_HINT_RE.search(text))


def _split_keywords(text: str) -> list[str]:
    if not text:
        return []
    return _as_str_list(re.split(r"[\s,，、\n]+", str(text)), max_items=20, max_len=40)


def _infer_full_scope_subject(question: str, filters: list[SemanticIntentFilter]) -> str:
    # 尝试在被错归类为 filter 的项中提取潜在的主语
    for item in filters:
        phrase = _as_clean_str(item.phrase)
        if phrase in {"所有", "全部", "全量", "所有的", "全部的", "全量的"}:
            continue
        for prefix in ["所有的", "全部的", "全量的", "所有", "全部", "全量"]:
            if phrase.startswith(prefix) and len(phrase) > len(prefix):
                return _as_clean_str(phrase[len(prefix):].lstrip("的"))
    match = re.search(r"(?:所有|全部|全量)(?:的)?([^，,。？?\s]+?)(?:的)?(?:列表|清单|明细)", question)
    if match:
        return _as_clean_str(match.group(1))
    return ""


def _sanitize_intent_scope(intent: DataQuerySemanticIntent, *, fallback_question: str = "") -> DataQuerySemanticIntent:
    question = _as_clean_str(fallback_question or intent.goal, max_len=240)
    
    # 1. 移除纯粹的“所有/全部”等没有真实业务约束力的空洞 filter
    intent.filters = [item for item in intent.filters if not _is_full_scope_phrase(item.phrase)]

    if not _is_full_scope_list_query(question):
        return intent

    subject = _infer_full_scope_subject(question, intent.filters)
    
    # 2. 清除 keywords 里的范围词前缀与噪声词，并插入主语和“列表”字样作为兜底 keywords
    user_anchored_keywords: list[str] = []
    seen_keywords: set[str] = set()
    for keyword in _split_keywords(intent.keywords):
        normalized = _as_clean_str(keyword)
        for prefix in ["所有的", "全部的", "全量的", "所有", "全部", "全量"]:
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                normalized = _as_clean_str(normalized[len(prefix):].lstrip("的"))
                break
        if not normalized or _is_full_scope_phrase(normalized):
            continue
        if normalized in seen_keywords:
            continue
        seen_keywords.add(normalized)
        user_anchored_keywords.append(normalized)
        
    if subject and subject not in seen_keywords and not _is_full_scope_phrase(subject):
        user_anchored_keywords.insert(0, subject)
        seen_keywords.add(subject)
    if "列表" in question and "列表" not in seen_keywords:
        user_anchored_keywords.append("列表")
        
    if user_anchored_keywords:
        intent.keywords = " ".join(user_anchored_keywords[:8])

    # 3. 维度清洗：移除空值和重复，不再强行剔除推导维度，保留大模型对关联维度的泛化推导能力
    cleaned_dimensions: list[str] = []
    seen_dimensions: set[str] = set()
    for dimension in intent.dimensions:
        text = _as_clean_str(dimension)
        if not text or text in seen_dimensions:
            continue
        seen_dimensions.add(text)
        cleaned_dimensions.append(text)
    if cleaned_dimensions:
        intent.dimensions = cleaned_dimensions[:8]

    return intent


def _extract_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_semantic_intent_payload(content: str, *, fallback_question: str = "") -> DataQuerySemanticIntent:
    data = _extract_json_object(content)
    filters: list[SemanticIntentFilter] = []
    raw_filters = data.get("filters") if isinstance(data, dict) else None
    if isinstance(raw_filters, list):
        for raw in raw_filters[:8]:
            if not isinstance(raw, dict):
                continue
            phrase = _as_clean_str(raw.get("phrase") or raw.get("value") or raw.get("condition"))
            if not phrase:
                continue
            filters.append(
                SemanticIntentFilter(
                    phrase=phrase,
                    semantic_type=_as_clean_str(raw.get("semantic_type") or raw.get("type")),
                    expected_column_types=_as_str_list(
                        raw.get("expected_column_types") or raw.get("preferred_columns") or raw.get("expected_columns")
                    ),
                    avoid_column_types=_as_str_list(
                        raw.get("avoid_column_types") or raw.get("avoid_columns") or raw.get("wrong_columns")
                    ),
                    relation=_as_clean_str(raw.get("relation")),
                )
            )
    keywords = _as_clean_str(data.get("keywords"), max_len=300)
    intent = DataQuerySemanticIntent(
        goal=_as_clean_str(data.get("goal") or fallback_question, max_len=200),
        keywords=keywords,
        metrics=_as_str_list(data.get("metrics")),
        dimensions=_as_str_list(data.get("dimensions")),
        filters=filters,
        time_range=_as_clean_str(data.get("time_range"), max_len=80),
        grain=_as_clean_str(data.get("grain"), max_len=80),
        target_datasets=_as_str_list(data.get("target_datasets")),
        reasoning=_as_clean_str(data.get("reasoning"), max_len=200),
    )
    return _sanitize_intent_scope(intent, fallback_question=fallback_question)


def derive_keywords_from_semantic_intent(intent: DataQuerySemanticIntent | None, *, max_terms: int = 12) -> str:
    if not intent or not intent.has_content():
        return ""
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = _as_clean_str(value, max_len=40)
        if not text or text in seen:
            return
        seen.add(text)
        terms.append(text)

    for metric in intent.metrics:
        add(metric)
    for dimension in intent.dimensions:
        add(dimension)
    for item in intent.filters:
        for expected in item.expected_column_types:
            add(expected)
    if intent.grain:
        add(intent.grain)
    for ds in intent.target_datasets:
        add(ds)
    if not terms and intent.goal:
        for token in re.split(r"[\s,，、]+", intent.goal):
            add(token)
    return " ".join(terms[:max_terms])


def semantic_intent_to_dict(intent: DataQuerySemanticIntent | None) -> dict[str, Any]:
    if not intent:
        return {}
    return asdict(intent)


def semantic_intent_from_dict(data: Any) -> DataQuerySemanticIntent | None:
    if not isinstance(data, dict):
        return None
    filters: list[SemanticIntentFilter] = []
    for item in data.get("filters") or []:
        if not isinstance(item, dict):
            continue
        phrase = _as_clean_str(item.get("phrase"))
        if not phrase:
            continue
        filters.append(
            SemanticIntentFilter(
                phrase=phrase,
                semantic_type=_as_clean_str(item.get("semantic_type")),
                expected_column_types=_as_str_list(item.get("expected_column_types")),
                avoid_column_types=_as_str_list(item.get("avoid_column_types")),
                relation=_as_clean_str(item.get("relation")),
            )
        )
    intent = DataQuerySemanticIntent(
        goal=_as_clean_str(data.get("goal"), max_len=200),
        keywords=_as_clean_str(data.get("keywords"), max_len=300),
        metrics=_as_str_list(data.get("metrics")),
        dimensions=_as_str_list(data.get("dimensions")),
        filters=filters,
        time_range=_as_clean_str(data.get("time_range"), max_len=80),
        grain=_as_clean_str(data.get("grain"), max_len=80),
        target_datasets=_as_str_list(data.get("target_datasets")),
        reasoning=_as_clean_str(data.get("reasoning"), max_len=200),
    )
    return intent if intent.has_content() else None


def build_semantic_intent_prompt(
    user_question: str,
    standalone_query: str,
    example_context: str,
    conversation_context: str = "",
    available_datasets: str = "",
) -> str:
    context_block = ""
    if str(conversation_context or "").strip():
        context_block = (
            f"\n\n【最近对话上下文】\n{conversation_context.strip()}\n\n"
            "【最新提问优先级】\n"
            "最近对话只用于补全省略、指代、延续的业务对象、指标、维度和筛选条件；"
            "若最近对话与【独立查数问题】或【用户原始问题】冲突，必须以最新提问新增/修改的条件为准。"
        )
    return (
        "你是 ChatBI 的结构化业务意图分析器。你的任务不是生成 SQL，而是在执行 get_dataset_schema 和 SQL 之前，"
        "把用户查数需求拆成可复核的业务意图帧。\n\n"
        "请识别：业务目标、指标、维度、筛选条件、时间范围、聚合粒度，以及每个筛选条件应优先绑定的字段语义。\n\n"
        "只返回 JSON，不要 Markdown。格式如下：\n"
        '{"keywords":"用于 get_dataset_schema 的 3-10 个关键词",'
        '"goal":"用户业务目标","metrics":["指标"],"dimensions":["维度"],'
        '"filters":[{"phrase":"用户筛选原词","semantic_type":"geographic_region|organization|customer|status|category|entity|time|other",'
        '"expected_column_types":["应优先绑定的字段语义或常见物理名"],'
        '"avoid_column_types":["容易误绑的字段语义或物理名"],'
        '"relation":"exact_value|alias|parent_region_or_scope|category_scope|contains|unknown"}],'
        '"time_range":"时间范围或无","grain":"聚合粒度或明细粒度",'
        '"target_datasets":["猜测的目标数据集名称"],"reasoning":"一句话说明"}\n\n'
        "【核心约束原则】\n"
        "1. 严格限制 keywords 和 dimensions，严禁脑补与需求扩大化：\n"
        "   - keywords 仅用于匹配元数据的表名、字段名和字段描述。严禁包含具体的业务数据值（如具体的人名、特定的日期/时间、具体的金额数值等）。对于带有具体值的过滤条件，应提取其背后的业务属性概念（如将‘蒋公律’提取为‘员工/跟进人’，将‘5月份’提取为‘时间/月份’）作为 keyword。如果用户意图明显属于某个数据集，必须将其名称包含在 keywords 中以提高检索精度。\n"
        "   - target_datasets 必须从【可用数据集菜单】中挑选最符合用户意图的一个或多个数据集名称。如果不确定则为空。\n"
        "   - dimensions 必须是用户原始问题中显式提到或强烈暗示的维度（例如：用户问“各区域的销售额”，则 dimensions 包含“区域”）。如果用户仅请求了“所有/全部 + 对象 + 列表/明细”（如“所有客户列表”），而没有提到任何具体的属性或维度，则 dimensions 只能输出对应的对象实体名称本身（如“客户”），严禁擅自脑补或补充其他具体属性字段（如“客户等级”、“客户地址”等）作为 dimensions。\n"
        "2. 正确区分“全量范围”与“过滤条件”：\n"
        "   - filters 仅用于表示过滤/筛选条件（即限制数据范围的具体条件，如特定的时间、特定的空间、特定的状态、特定的属性取值等）。\n"
        "   - “所有/全部/全量 + 对象”表示的是全量查询范围，而不是筛选条件。不要把代表全量范围的词（如“所有客户”、“全部产品”）输出到 filters 中。\n"
        "3. DataQueryIntentFrame 不是数据库 Schema，不得编造物理表名、物理字段名或 JOIN 键；SQL 的 FROM/JOIN/字段必须以 get_dataset_schema 返回为准。\n"
        "4. expected_column_types 只能写字段语义或候选字段名线索，例如 区域/region/area/province/city；这些线索不是已确认物理字段名，须以 Schema 返回为准。\n"
        "5. avoid_column_types 用于指出容易误绑字段，例如 地域/组织条件不要优先绑到实体名称/name/title 等展示字段。\n"
        "6. 若无法判断字段语义，relation 使用 unknown，但不要编造具体数据值。\n\n"
        "【示例对比学习】\n"
        "示例一：无额外筛选条件的全量明细列表查询\n"
        "用户原始问题：\"查一下所有客户的列表\"\n"
        "期望 JSON 输出：\n"
        "{\n"
        '  "keywords": "客户 列表",\n'
        '  "goal": "获取所有客户的基础信息列表",\n'
        '  "metrics": [],\n'
        '  "dimensions": ["客户"],\n'
        '  "filters": [],\n'
        '  "time_range": "无",\n'
        '  "grain": "明细粒度",\n'
        '  "target_datasets": [],\n'
        '  "reasoning": "用户只要求获取所有客户的基础信息列表，没有提及任何具体的属性字段（如等级或地址），也没有过滤限制条件（“所有客户”代表全量范围，不应提取为 filter）。"\n'
        "}\n\n"
        "示例二：带具体筛选条件的列表查询\n"
        "用户原始问题：\"列出所有北京在售状态的产品\"\n"
        "期望 JSON 输出：\n"
        "{\n"
        '  "keywords": "北京 在售 产品 列表",\n'
        '  "goal": "获取所有北京在售状态的产品明细列表",\n'
        '  "metrics": [],\n'
        '  "dimensions": ["产品"],\n'
        '  "filters": [\n'
        '    {"phrase": "北京", "semantic_type": "geographic_region", "expected_column_types": ["区域", "city", "region", "province"], "relation": "exact_value"},\n'
        '    {"phrase": "在售", "semantic_type": "status", "expected_column_types": ["状态", "status"], "relation": "exact_value"}\n'
        '  ],\n'
        '  "time_range": "无",\n'
        '  "grain": "明细粒度",\n'
        '  "target_datasets": [],\n'
        '  "reasoning": "用户要求获取产品的明细列表，但限定了北京地域和在售状态，应将其作为 filters 提取。而“所有”表示全量范围，不应作为 filter 提取。"\n'
        "}\n\n"
        f"【用户原始问题】\n{user_question}\n\n"
        f"【独立查数问题】\n{standalone_query}\n\n"
        f"【命中的历史案例线索】\n{example_context}\n\n"
        f"【可用数据集菜单】\n{available_datasets or '（未提供，请根据业务常识推理）'}"
        f"{context_block}"
    )


def format_semantic_intent_context(intent: DataQuerySemanticIntent | None) -> str:
    if not intent or not intent.has_content():
        return ""
    lines = ["【结构化业务意图（用于字段绑定自检，不是 SQL 结果）】"]
    if intent.goal:
        lines.append(f"- 业务目标: {intent.goal}")
    if intent.metrics:
        lines.append(f"- 指标: {'、'.join(intent.metrics)}")
    if intent.dimensions:
        lines.append(f"- 维度: {'、'.join(intent.dimensions)}")
    if intent.time_range:
        lines.append(f"- 时间范围: {intent.time_range}")
    if intent.grain:
        lines.append(f"- 粒度: {intent.grain}")
    if intent.target_datasets:
        lines.append(f"- 猜测目标数据集: {'、'.join(intent.target_datasets)}")
    for item in intent.filters:
        detail = f"- 筛选: `{item.phrase}`"
        if item.semantic_type:
            detail += f"；语义类型: {item.semantic_type}"
        if item.relation:
            detail += f"；关系: {item.relation}"
        if item.expected_column_types:
            detail += f"；优先绑定字段语义: {'、'.join(item.expected_column_types)}"
        if item.avoid_column_types:
            detail += f"；避免误绑: {'、'.join(item.avoid_column_types)}"
        lines.append(detail)
    lines.append(
        "注意：本意图帧不是已确认物理表名或字段名来源；"
        "expected_column_types / avoid_column_types 只是字段语义线索，SQL 的 FROM/JOIN/字段必须以 get_dataset_schema 返回为准。"
    )
    lines.append(
        "生成 SQL 前请逐项核对：每个用户筛选词是否绑定到了语义匹配的字段；"
        "若字段语义不匹配，应先重查 Schema 或更换字段，而不是直接把口语词写进任意名称字段。"
    )
    return "\n".join(lines)


def _preview_diagnostics(diagnostics: list[dict[str, Any]], *, max_items: int = 3) -> list[str]:
    lines: list[str] = []
    for item in diagnostics[:max_items]:
        column = _as_clean_str(item.get("column"))
        used_values = "、".join(_as_str_list(item.get("used_values"), max_items=4))
        candidates = "、".join(_as_str_list(item.get("candidates"), max_items=6))
        alternatives = "、".join(_as_str_list(item.get("alternative_columns"), max_items=6))
        parts = [f"字段 `{column}`" if column else "字段（未知）"]
        if used_values:
            parts.append(f"使用值: {used_values}")
        if candidates:
            parts.append(f"候选值: {candidates}")
        if alternatives:
            parts.append(f"备选字段: {alternatives}")
        lines.append("- " + "；".join(parts))
    return lines


def format_empty_result_semantic_repair_context(
    intent: DataQuerySemanticIntent | None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> str:
    if not intent or not intent.has_content():
        return ""
    lines = ["【空结果语义复核】请基于原始业务意图重新判断 WHERE 条件，而不是只做字符串候选值匹配。"]
    if intent.goal:
        lines.append(f"- 原始目标: {intent.goal}")
    for item in intent.filters:
        relation_note = ""
        if item.relation in {"parent_region_or_scope", "category_scope"}:
            relation_note = "；这是父级/范围条件，候选明细值可能不会直接包含用户原词"
        lines.append(
            f"- 用户筛选 `{item.phrase}`: 类型 {item.semantic_type or 'unknown'}，关系 {item.relation or 'unknown'}"
            f"{relation_note}"
        )
        if item.expected_column_types:
            lines.append(f"  优先核对字段语义: {'、'.join(item.expected_column_types)}")
        if item.avoid_column_types:
            lines.append(f"  避免误绑字段: {'、'.join(item.avoid_column_types)}")
    if diagnostics:
        lines.append("【本次空结果候选证据摘要】")
        lines.extend(_preview_diagnostics(diagnostics))
    lines.append(
        "修复要求：先判断用户筛选词是精确值、别名、父级/范围条件还是分类条件；"
        "不能仅因候选值不包含原词就判定无数据。若候选值像下级实体，请优先查找区域/组织/分类字段，"
        "或用诊断 SQL 证明父子关系后再修正最终 SQL。"
    )
    return "\n".join(lines)
