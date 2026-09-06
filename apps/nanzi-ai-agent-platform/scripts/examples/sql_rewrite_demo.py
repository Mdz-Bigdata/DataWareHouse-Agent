import sqlglot
from sqlglot import exp, parse_one

def rewrite_sql_with_filters(original_sql, filters, user_context, dialect="clickhouse"):
    """
    使用 sqlglot 动态重写 SQL，注入行级权限条件。
    """
    # 1. 解析原始 SQL
    expression = parse_one(original_sql, read=dialect)
    
    # 2. 准备要注入的条件片段
    processed_conditions = []
    for f in filters:
        cond_str = f["condition"]
        # 简单的模板替换: {user.xxx} -> 实际值
        # 注意：实际实现中需要处理 SQL 注入防御，这里仅作演示
        for key, value in user_context.items():
            placeholder = f"{{user.{key}}}"
            if placeholder in cond_str:
                # 根据类型决定是否加引号
                val_formatted = f"'{value}'" if isinstance(value, str) else str(value)
                cond_str = cond_str.replace(placeholder, val_formatted)
        
        processed_conditions.append(parse_one(cond_str, read=dialect))

    # 3. 寻找所有的 Table 节点并注入 WHERE
    # 在 sqlglot 中，我们可以遍历 Select 语句，并在其 WHERE 子句中追加条件
    for select in expression.find_all(exp.Select):
        # 简单起见，这里将所有条件用 AND 连接后注入
        for cond in processed_conditions:
            select.where(cond, append=True)
            
    return expression.sql(dialect=dialect)

# --- 演示场景 ---

# 模型生成的原始 SQL
raw_sql = "SELECT server_name, ip FROM sys_server_assets WHERE cpu_usage > 80"

# 数据集配置的权限过滤规则
row_filters = [
    {"target_table": "all", "condition": "region_code = {user.region}"},
    {"target_table": "all", "condition": "status = 1"}
]

# 当前登录用户的上下文（从 JWT 或数据库获取）
current_user = {
    "region": "SH",
    "org_path": "yovole/sh/tech"
}

# 执行改写
safe_sql = rewrite_sql_with_filters(raw_sql, row_filters, current_user)

print(f"原始 SQL: {raw_sql}")
print(f"安全 SQL: {safe_sql}")
