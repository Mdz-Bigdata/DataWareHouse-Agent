"""ODS 行的系统字段盖章与表结构对齐。

两件事：
  1. **盖章**：三条通道（CDC / Kafka / OSS 合规上传）写入 ODS 的每一行，都必须带上
     ``_ingest_time``（入湖时间）与 ``_source_system``（来源系统标识）。这是
     ``domains.Layer.ODS.system_fields`` 定的层规范，不是各通道自己的约定——
     [a5] 第六章「通道可以分，门禁不能分」在字段层面的对应物。
  2. **对齐**：把通道侧的宽字典投影到注册表里真实存在的列上。注册表
     (``catalog.registry``) 是只读共享契约，通道侧多出来的字段（例如合规标记、
     storage_class）不能凭空写进湖表，只能留在入湖侧对象里供门禁使用。

注册表是**延迟 import** 的：其他数据域的表模块由别的子系统陆续登记，
本模块不能因为它们尚未就绪而在 import 期炸掉。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ..domains import Layer

__all__ = [
    "SYS_INGEST_TIME",
    "SYS_SOURCE_SYSTEM",
    "SYS_UPDATE_TIME",
    "stamp_system_fields",
    "registered_columns",
    "project_to_table",
    "dropped_fields",
]

#: 系统字段名——与 catalog.spec.SYSTEM_COLUMNS 保持一致（契约只读，此处只引用名字）
SYS_INGEST_TIME = "_ingest_time"
SYS_SOURCE_SYSTEM = "_source_system"
SYS_UPDATE_TIME = "update_time"


def stamp_system_fields(
    row: Mapping[str, Any],
    *,
    source_system: str,
    ingest_time: datetime | None = None,
    layer: Layer = Layer.ODS,
) -> dict[str, Any]:
    """给一行数据盖上该层的系统字段，返回新字典（不改入参）。

    Args:
        row: 通道解析出的业务字段。
        source_system: ``_source_system`` 取值。ODS 层必填——
            ``catalog.spec.TableSpec.validate()`` 会校验表级声明，此处校验行级取值。
        ingest_time: ``_ingest_time`` 取值，缺省取当前时间。
        layer: 目标层，决定追加哪组系统字段（ODS 是 _ingest_time + _source_system，
            其余层是 _ingest_time + update_time）。

    Raises:
        ValueError: ODS 层未给 source_system。

    Note:
        业务字段优先：若 ``row`` 里已经带了同名字段（例如 CDC 源表自带 update_time），
        保留原值不覆盖，与 ``TableSpec.all_columns()`` 的去重口径一致。
    """
    if layer is Layer.ODS and not source_system:
        raise ValueError("ODS 层每一行都必须带 _source_system（来源系统标识），不能为空")

    out = dict(row)
    fields = layer.system_fields
    if SYS_INGEST_TIME in fields:
        out.setdefault(SYS_INGEST_TIME, ingest_time or datetime.now())
    if SYS_SOURCE_SYSTEM in fields:
        out.setdefault(SYS_SOURCE_SYSTEM, source_system)
    if SYS_UPDATE_TIME in fields:
        out.setdefault(SYS_UPDATE_TIME, None)
    return out


def registered_columns(table_name: str) -> tuple[str, ...] | None:
    """查注册表里这张表的全部列名（含系统字段）。表未登记或注册表不可用时返回 None。

    延迟 import + 宽异常捕获：其余数据域的表模块由其他子系统登记，尚未就绪时
    本函数只是「查不到」，不应影响入湖链路本身。
    """
    try:
        from ..catalog.registry import by_name
    except Exception:  # pragma: no cover - 注册表模块本身不可用
        return None
    try:
        spec = by_name(table_name)
    except Exception:  # KeyError=未登记；其他异常=该域表模块尚未就绪
        return None
    return tuple(c.name for c in spec.all_columns())


def project_to_table(
    table_name: str,
    row: Mapping[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """把一行宽字典投影到目标表真实存在的列上。

    Args:
        table_name: 目标 Paimon 表名，例如 ``ods_data_file_meta``。
        row: 盖过章的行。
        strict: True 时，表里有而行里没有的列会抛错（用于 NOT NULL 列的早失败）；
            False 时缺列留空由引擎处理。

    Returns:
        仅含注册列的新字典。注册表查不到该表时原样返回（降级为「通道自述结构」）。

    Raises:
        ValueError: strict=True 且缺列。
    """
    cols = registered_columns(table_name)
    if cols is None:
        return dict(row)
    projected = {c: row[c] for c in cols if c in row}
    if strict:
        missing = [c for c in cols if c not in row]
        if missing:
            raise ValueError(f"{table_name} 缺少字段: {missing}")
    return projected


def dropped_fields(table_name: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    """列出投影时被丢弃的字段，便于在日志里解释「为什么这个字段没进湖」。

    典型场景：[a8] 强调的 ``storage_class`` 与双合规标记，在共享契约的
    ``ods_data_file_meta`` 中尚无对应列（契约只读，不得改动），它们会被本函数列出。
    """
    cols = registered_columns(table_name)
    if cols is None:
        return ()
    return tuple(k for k in row if k not in cols)
