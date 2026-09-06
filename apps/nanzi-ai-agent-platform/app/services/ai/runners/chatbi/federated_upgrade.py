"""Federated query upgrade heuristics."""

from __future__ import annotations

import re


def extract_schema_dataset_names(schema_output: str) -> set[str]:
    return {
        match.strip()
        for match in re.findall(r"^\s*dataset:\s*([^\s]+)", schema_output or "", re.MULTILINE)
        if match.strip()
    }


def looks_like_explicit_federated_query(user_question: str) -> bool:
    q = (user_question or "").strip().lower()
    if not q:
        return False
    explicit_terms = (
        "跨数据集",
        "跨库",
        "跨源",
        "联邦查询",
        "多数据集",
        "多个数据集",
        "不同数据集",
        "不同库",
        "联合查询",
    )
    return any(term in q for term in explicit_terms)


def should_upgrade_to_federated_query(schema_output: str, user_question: str) -> bool:
    return (
        len(extract_schema_dataset_names(schema_output)) > 1
        and looks_like_explicit_federated_query(user_question)
    )
