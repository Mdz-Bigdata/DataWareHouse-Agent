from dataclasses import dataclass


@dataclass
class FeedbackEntry:
    """Reviewed SQL template or quarantined auto-repair candidate.

    存储一条"曾经失败的 SQL + 最终修复成功的 SQL"经验，用于在 correct_sql
    节点召回相似问题的历史修复方案，辅助 LLM 自愈纠错。

    存储位置：Qdrant feedback 集合（独立于 column/metric 集合，不随知识库
    rebuild 重建——它是运行时持续积累的经验）。

    向量构造：question 文本（不含 SQL，避免 SQL 语法噪声干扰语义匹配）。
    """

    id: str  # 唯一标识，一般用 question 的 sha256
    question: str  # 已脱敏问题，不保存原始敏感值
    error_sql: str  # 参数化、移除 RLS 后的失败 SQL 模板
    corrected_sql: str  # 参数化、移除 RLS 后的修复 SQL 模板
    error_message: str  # 已脱敏错误摘要
    table_signature: str  # 涉及的表集合签名（如 "album,play_session"），辅助过滤
    error_parameter_types: tuple[str, ...] = ()
    corrected_parameter_types: tuple[str, ...] = ()
    lifecycle: str = "candidate"  # candidate / published / disabled
    source: str = "auto_repair"
