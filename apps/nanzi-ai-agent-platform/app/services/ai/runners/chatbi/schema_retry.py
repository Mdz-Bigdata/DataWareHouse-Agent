"""Controlled schema retry keyword extraction."""

from __future__ import annotations

import re
from typing import Any

from app.services.ai.runners.chatbi.constants import SCHEMA_RETRY_STOPWORDS, SCHEMA_RETRY_SUFFIXES
from app.services.ai.runners.chatbi.run_state import DataRunState


def append_unique_keyword(tokens: list[str], keyword: str) -> None:
    normalized = " ".join(str(keyword or "").strip().split())
    if normalized and normalized not in tokens:
        tokens.append(normalized)


def clean_schema_retry_phrase(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\d{4}[-/年]\d{1,2}[-/月]?\d{0,2}日?", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}月\d{0,2}日?", " ", cleaned)
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]+", " ", cleaned)
    for word in SCHEMA_RETRY_STOPWORDS:
        cleaned = cleaned.replace(word, " ")
    cleaned = re.sub(r"[?？!！。；;：:，,、\[\]（）(){}<>《》\"'`]+", " ", cleaned)
    return " ".join(cleaned.split())


def schema_keywords_from_args(tool_args: dict[str, Any] | None) -> str:
    if not isinstance(tool_args, dict):
        return ""
    raw = tool_args.get("keywords") or tool_args.get("query") or tool_args.get("input")
    if isinstance(raw, list):
        return " ".join(str(item).strip() for item in raw if str(item).strip())
    return str(raw or "").strip()


def schema_retry_core_terms(*sources: Any) -> list[str]:
    terms: list[str] = []
    preserved_phrases: list[str] = []
    requested_suffixes: list[str] = []
    for source in sources:
        source_text = str(source or "").strip()
        if not source_text:
            continue
        for phrase in re.split(r"[\s,，、;；]+", source_text):
            phrase = phrase.strip()
            if not phrase:
                continue
            if phrase in SCHEMA_RETRY_SUFFIXES:
                append_unique_keyword(requested_suffixes, phrase)
                continue
            compact_metric = False
            for suffix in SCHEMA_RETRY_SUFFIXES:
                if phrase.endswith(suffix) and len(phrase) > len(suffix):
                    stem = phrase[: -len(suffix)].strip()
                    if re.search(r"[A-Za-z]", stem):
                        compact_metric = True
                        append_unique_keyword(preserved_phrases, phrase)
                    else:
                        append_unique_keyword(requested_suffixes, suffix)
                    break
            if compact_metric:
                continue
            cleaned = clean_schema_retry_phrase(phrase)
            if cleaned:
                for term in cleaned.split():
                    append_unique_keyword(terms, term)
                if (
                    cleaned != phrase
                    and len(phrase) <= 16
                    and phrase.startswith(("所有", "全部"))
                ):
                    append_unique_keyword(preserved_phrases, phrase)
    return [
        *terms,
        *[f"__phrase__{phrase}" for phrase in preserved_phrases],
        *[f"__suffix__{suffix}" for suffix in requested_suffixes],
    ]


def build_controlled_schema_retry_keywords(*sources: Any) -> str:
    terms_and_markers = schema_retry_core_terms(*sources)
    if not terms_and_markers:
        return ""
    requested_suffixes = [
        value.removeprefix("__suffix__")
        for value in terms_and_markers
        if value.startswith("__suffix__")
    ]
    preserved_phrases = [
        value.removeprefix("__phrase__")
        for value in terms_and_markers
        if value.startswith("__phrase__")
    ]
    core_terms = [
        value
        for value in terms_and_markers
        if not value.startswith("__suffix__") and not value.startswith("__phrase__")
    ]
    if not core_terms and not preserved_phrases:
        return ""

    tokens: list[str] = []
    for term in core_terms:
        append_unique_keyword(tokens, term)
    for phrase in preserved_phrases:
        append_unique_keyword(tokens, phrase)
    base_terms = [
        term
        for term in core_terms
        if len(term) <= 12 and not any(term.endswith(suffix) for suffix in SCHEMA_RETRY_SUFFIXES)
    ]
    if len(base_terms) >= 2 and not requested_suffixes:
        append_unique_keyword(tokens, "".join(base_terms[:2]))
    for term in base_terms[:4]:
        for suffix in requested_suffixes:
            append_unique_keyword(tokens, f"{term}{suffix}")
    return " ".join(tokens[:32])


def prepare_controlled_schema_retry_keywords(
    state: DataRunState,
    *,
    schema_search_keywords: str = "",
    standalone_query: str = "",
    user_question: str = "",
) -> None:
    sources: list[str] = []
    if state.last_schema_keywords:
        sources.append(state.last_schema_keywords)
    if schema_search_keywords:
        sources.append(schema_search_keywords)

    if not sources:
        if standalone_query:
            sources.append(standalone_query)
        if user_question:
            sources.append(user_question)

    retry_keywords = build_controlled_schema_retry_keywords(*sources)
    state.controlled_schema_retry_keywords = retry_keywords
    state.pending_schema_retry = bool(retry_keywords.strip())


def record_schema_keywords(
    state: DataRunState,
    tool_args: dict[str, Any] | None,
) -> None:
    keywords = str(state.last_applied_schema_retry_keywords or "").strip()
    if not keywords:
        keywords = schema_keywords_from_args(tool_args)
    if keywords:
        state.last_schema_keywords = keywords
