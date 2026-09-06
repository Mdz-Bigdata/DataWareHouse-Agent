"""
ChatBI 元数据 Schema 检索块格式化：统一工具输出、HTTP API 与后台 YAML 导出的分隔头。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 新格式：--- [Schema:1] type=table dataset=sales_detail table=dim_region score=0.86 ---
_SCHEMA_HEADER_RE = re.compile(
    r"---\s*\[Schema:(\d+)\]\s+"
    r"type=(\w+)"
    r"(?:\s+dataset=([^\s]+))?"
    r"(?:\s+table=([^\s]+))?"
    r"(?:\s+score=([0-9]+(?:\.[0-9]+)?))?"
    r"\s*---",
    re.IGNORECASE,
)

# 旧格式（兼容）：[置信度: 0.86] + --- Source: xxx ---
_LEGACY_CONFIDENCE_RE = re.compile(r"置信度[:：]\s*([0-9]+(?:\.[0-9]+)?)")
_LEGACY_BLOCK_RE = re.compile(
    r"\[置信度[:：]\s*([0-9]+(?:\.[0-9]+)?)\]\s*"
    r"(?:\n|\r\n)---\s*Source:\s*([^\n\r]+?)\s*---",
    re.IGNORECASE,
)

_TABLE_NAME_RE = re.compile(r"^table_name:\s*(\S+)\s*$", re.MULTILINE)
_DATASET_RE = re.compile(r"^dataset:\s*(\S+)\s*$", re.MULTILINE)
_METRICS_SCOPE_RE = re.compile(r"^metrics_scope:\s*(.+?)\s*$", re.MULTILINE)


def infer_schema_chunk_meta(content: str, doc_name: str = "") -> Tuple[str, Optional[str], Optional[str]]:
    """
    从 YAML 正文或 doc_name 推断块类型与标识。
    Returns: (chunk_type, table_name, dataset_name)
    """
    text = str(content or "")
    doc = str(doc_name or "").strip().lower()

    if doc.endswith("_metrics.txt") or "metrics_scope:" in text:
        dataset = None
        m_scope = _METRICS_SCOPE_RE.search(text)
        if m_scope:
            dataset = m_scope.group(1).strip()
        m_ds = _DATASET_RE.search(text)
        if m_ds:
            dataset = m_ds.group(1).strip()
        return "metrics", None, dataset

    table_name = None
    m_table = _TABLE_NAME_RE.search(text)
    if m_table:
        table_name = m_table.group(1).strip()
    elif doc.endswith(".txt"):
        table_name = doc.rsplit(".", 1)[0].split(".")[-1] or None

    dataset = None
    m_ds = _DATASET_RE.search(text)
    if m_ds:
        dataset = m_ds.group(1).strip()
    elif doc and "." in doc:
        dataset = doc.rsplit(".", 1)[0]

    return "table", table_name, dataset


def format_schema_chunk(
    index: int,
    content: str,
    *,
    score: Optional[float] = None,
    doc_name: str = "",
    chunk_type: Optional[str] = None,
    table_name: Optional[str] = None,
    dataset: Optional[str] = None,
) -> str:
    """将单段 YAML 正文包装为带分隔头的 Schema 检索块。"""
    body = str(content or "").strip()
    if not body:
        return ""

    inferred_type, inferred_table, inferred_dataset = infer_schema_chunk_meta(body, doc_name)
    ctype = chunk_type or inferred_type
    tbl = table_name or inferred_table
    ds = dataset or inferred_dataset

    parts = [f"--- [Schema:{index}]", f"type={ctype}"]
    if ds:
        parts.append(f"dataset={ds}")
    if tbl:
        parts.append(f"table={tbl}")
    if score is not None:
        try:
            parts.append(f"score={float(score):.2f}")
        except (TypeError, ValueError):
            pass
    header = " ".join(parts) + " ---"
    return f"{header}\n{body}"


def format_schema_hits(hits: List[Dict[str, Any]]) -> str:
    """批量格式化检索命中（每项含 content、similarity、doc_name）。"""
    chunks: List[str] = []
    for idx, hit in enumerate(hits, start=1):
        content = str(hit.get("content") or "").strip()
        if not content:
            continue
        try:
            score = float(hit.get("similarity", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = None
        formatted = format_schema_chunk(
            idx,
            content,
            score=score,
            doc_name=str(hit.get("doc_name") or ""),
        )
        if formatted:
            chunks.append(formatted)
    return "\n\n".join(chunks)


def count_schema_hits(text: Any) -> int:
    """统计 Schema 工具输出中的元数据块数量（兼容新旧分隔头）。"""
    raw = str(text or "")
    new_count = len(_SCHEMA_HEADER_RE.findall(raw))
    if new_count:
        return new_count
    return len(_LEGACY_BLOCK_RE.findall(raw))


def estimate_text_tokens(text: Any) -> int:
    """粗略估算文本 token 数（用于日志展示，非计费精度）。"""
    raw = str(text or "")
    if not raw:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", raw))
    other = len(raw) - cjk
    return max(1, int(cjk * 1.5 + other / 4))


def format_schema_hit_summary(text: Any) -> Optional[str]:
    """生成工具日志用的命中摘要行；无命中块时返回 None。"""
    hit_count = count_schema_hits(text)
    if hit_count <= 0:
        return None
    token_est = estimate_text_tokens(text)
    return f"[命中摘要] 共命中 {hit_count} 条元数据记录，占用约 {token_est} token"


def extract_schema_confidence_values(text: Any) -> List[float]:
    """从 Schema 工具输出中提取置信度/score（兼容新旧格式）。"""
    raw = str(text or "")
    values: List[float] = []

    for match in _SCHEMA_HEADER_RE.finditer(raw):
        score_text = match.group(5)
        if score_text:
            try:
                values.append(float(score_text))
            except ValueError:
                continue

    for value in _LEGACY_CONFIDENCE_RE.findall(raw):
        try:
            values.append(float(value))
        except ValueError:
            continue

    return values


def _candidate_key(chunk_type: str, dataset: Optional[str], table: Optional[str], source: str) -> str:
    ctype = (chunk_type or "table").lower()
    ds = (dataset or "").strip().lower()
    tbl = (table or "").strip().lower()
    if ctype == "metrics":
        return f"metrics:{ds or source.strip().lower()}"
    if ds and tbl:
        return f"{ds}.{tbl}"
    if tbl:
        return tbl
    return source.strip().lower()


def detect_schema_ambiguity(tool_output: Any) -> Tuple[bool, str]:
    """
    检测多个高置信度 Schema 候选是否分数接近且指向不同对象。
    兼容新旧分隔头格式。
    """
    text = str(tool_output or "").strip()
    if not text:
        return False, ""

    candidates: List[Tuple[float, str]] = []

    for match in _SCHEMA_HEADER_RE.finditer(text):
        try:
            score = float(match.group(5) or 0)
        except ValueError:
            continue
        chunk_type = match.group(2) or "table"
        dataset = match.group(3)
        table = match.group(4)
        key = _candidate_key(chunk_type, dataset, table, f"schema_{match.group(1)}")
        if key:
            candidates.append((score, key))

    for score_text, source in _LEGACY_BLOCK_RE.findall(text):
        try:
            score = float(score_text)
        except ValueError:
            continue
        source_norm = source.strip().lower()
        if source_norm:
            candidates.append((score, source_norm))

    if len(candidates) < 2:
        return False, ""

    candidates.sort(reverse=True)
    top_score, top_key = candidates[0]
    close_candidates = [
        (score, key)
        for score, key in candidates[1:]
        if score >= 0.75 and top_score - score <= 0.08 and key != top_key
    ]
    if top_score >= 0.75 and close_candidates:
        display = ", ".join([top_key, *[key for _, key in close_candidates[:2]]])
        return True, f"多个高置信度 Schema 候选分数接近：{display}"
    return False, ""
