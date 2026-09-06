"""SQL repair hint builders for ChatBI repair rounds."""

from __future__ import annotations

import re

from app.services.ai.runners.chatbi.sql_gates import (
    extract_invalid_sql_identifiers,
    is_cross_dataset_scope_sql_error,
    is_schema_reference_sql_error,
    is_where_condition_sql_error,
)


def invalid_identifier_repair_hint(message: str) -> str:
    identifiers = extract_invalid_sql_identifiers(message)
    if not identifiers:
        return ""
    return (
        "\n\n【无效标识符定位】本次数据库明确报出的无效字段/标识符："
        f"{', '.join(identifiers)}。请在 get_dataset_schema 返回的物理列名中逐项核对；"
        "若 schema 未列出这些字段，必须删除或替换为真实物理列，"
        "并同步修正 SELECT、JOIN、WHERE、GROUP BY、ORDER BY 中所有引用，"
        "不得继续使用这些字段名。"
    )


def cross_dataset_scope_repair_hint(message: str) -> str:
    if not is_cross_dataset_scope_sql_error(message):
        return ""
    return (
        "\n\n【跨数据集 SQL 修正要求】上一轮 execute_sql_query 尝试在单数据集 SQL 中引用其他数据集表，"
        "已被平台拦截。普通 execute_sql_query 只能查询当前 dataset 下的物理表，"
        "不要把其他数据集表写进同一条 SQL，也不要用 other_ds.table、跨库 schema 前缀或猜测表名绕过门禁。"
        "如果用户明确要求跨数据集/跨库/联合查询，请回到跨数据集联邦查询流程："
        "分别在各自 dataset 内生成子查询，再由联邦执行阶段做内存关联。"
        "如果只是为了展示姓名/部门/客户名称等维度信息，请改为只查询当前数据集的事实字段和外键字段，"
        "等待后端 relation/维表维度补全，不要手写跨数据集 JOIN。"
    )


def sql_repair_taxonomy_hint(message: str) -> str:
    text = str(message or "")
    lower = text.lower()
    if is_cross_dataset_scope_sql_error(text):
        category = "cross_dataset_scope"
        focus = "移除单数据集 SQL 中的跨数据集表引用，改走联邦查询或仅查询外键等待维度补全"
    elif is_where_condition_sql_error(text):
        category = "where_condition_mismatch"
        focus = "先读取源表 WHERE 相关列真实样例，再按样例格式重写比较/转换表达式"
    elif is_schema_reference_sql_error(text):
        category = "invalid_identifier"
        focus = "核对字段名、表名或别名引用"
    elif "not a group by" in lower or "group by expression" in lower or "ora-00979" in lower:
        category = "group_by_mismatch"
        focus = "核对 SELECT 中非聚合字段是否全部进入 GROUP BY，或改为聚合表达式"
    elif "join" in lower and ("cartesian" in lower or "missing" in lower or "condition" in lower):
        category = "join_condition_missing"
        focus = "补齐 JOIN ON 条件，并确认左右表关联键来自 Schema"
    elif "permission" in lower or "unauthorized" in lower or "access denied" in lower:
        category = "permission_denied"
        focus = "不要改写 SQL 绕过权限，应如实说明权限不足或请求授权"
    elif "syntax" in lower or "unexpected token" in lower or "invalid expression" in lower:
        category = "syntax_error"
        focus = "修正数据库方言语法、分页写法、函数名和括号结构"
    else:
        category = "sql_execution_error"
        focus = "根据数据库错误信息最小化修改 SQL，禁止无依据更换业务口径"
    return f"\n\n【SQL Repair Taxonomy】错误分类：{category}\n修复重点：{focus}。"
