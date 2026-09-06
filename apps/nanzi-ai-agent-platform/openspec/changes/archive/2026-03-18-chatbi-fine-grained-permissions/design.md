# 设计：ChatBI 精细化数据权限控制 (Row & Column Level Security)

本文档定义了 ChatBI 在执行数据查询时，如何通过 SQL 动态改写技术实现行级隔离和列级脱敏。

## 1. 数据库模型变更 (Database Schema)

### 1.1 用户表扩展 (ai_agent_users)
*   **新增字段**: `dept_id` (Integer), `org_path` (String)
*   **作用**: 作为行级过滤的常用维度。

### 1.2 数据集元数据扩展 (meta_datasets)
*   **新增字段**: 
    *   `enable_data_perm` (Boolean): 是否启用权限校验。
    *   `row_filter_config` (JSON): 存储权限策略。

#### row_filter_config 结构示例:
```json
{
  "user_overrides": {
    "101": [{"condition": "1=1"}] 
  },
  "role_policies": {
    "regional_viewer": [{"condition": "region_code = {user.region_code}"}]
  },
  "default_policy": [{"condition": "1=0"}]
}
```

## 2. SQL 重写逻辑 (SQL Rewriter)

核心引擎将使用 `sqlglot` 库处理 SQL AST（抽象语法树）。

### 2.1 注入点
在 `app/services/ai/tools/data_api.py` 的 `execute_sql_query` 函数中：
1.  **解析**: 将原始 SQL 解析为 AST。
2.  **提取维度**: 从 `AgentContext` 中提取当前用户的 `dept_id`、`region_code` 等。
3.  **应用策略**: 遍历 AST 中的所有 `Select` 节点，在其 `WHERE` 子句中追加由 `row_filter_config` 编译出的条件。
4.  **脱敏**: (后续扩展) 将敏感列替换为脱敏函数。
5.  **还原**: 将 AST 还原为 SQL 字符串。

## 3. 业务流程与追踪 (Trace & Observability)

### 3.1 Trace 日志增强
在 ChatBI 执行痕迹中新增 `DataPermissionCheck` 节点：
*   **Status**: `SUCCESS` / `BYPASS` / `DENIED`
*   **Policy Applied**: 命中具体哪条规则（如 "Role: regional_viewer"）。
*   **Details**: 注入了哪些条件片段。

### 3.2 错误处理
若改写过程中发生 SQL 语法冲突（例如模型写的别名与注入条件冲突），系统应：
1.  记录详细错误。
2.  向前端返回：“[权限校验失败] 查询语句构造异常，请联系管理员检查数据集策略”。

## 4. UI/UX 配置交互设计 (Admin Interface)

为了降低配置门槛，后台管理界面将采用 **可视化规则构建器 (Visual Rule Builder)**。

### 4.1 策略管理面板 (Strategy Management)
*   **分级切换**: 提供 `用户(User)` / `角色(Role)` / `默认(Default)` 三个管理 Tab。
*   **优先级标识**: 界面上明确标注“用户策略优先于角色策略”。

### 4.2 规则构建器 (Rule Builder)
每条过滤规则由三部分组成：
*   **目标表 (Scope)**: 下拉选择“全部表”或指定具体物理表。
*   **过滤条件 (Condition)**: 
    *   **左值**: 选择数据表字段（从元数据 `MetaColumn` 动态加载）。
    *   **操作符**: `等于(=)`, `不等于(!=)`, `包含(IN)`, `匹配(LIKE)`, `大于(>)` 等。
    *   **右值类型**: 
        *   **系统变量**: 下拉选择 `{user.dept_id}`, `{user.region_code}`, `{user.org_path}`。
        *   **自定义值**: 输入框（字符串、数字或日期）。

### 4.3 实时预览 (SQL Preview)
*   **功能**: 在配置规则时，提供一个“预览 SQL”按钮。
*   **逻辑**: 输入一段测试 SQL，系统实时展示经过当前规则改写后的 SQL 效果。

## 5. 后台管理接口 (Admin API)

*   **GET /api/v1/metadata/datasets/{id}/perm-config**: 获取当前配置。
*   **POST /api/v1/metadata/datasets/{id}/perm-config**: 保存配置。
*   **POST /api/v1/users/{id}/dept**: 快速更新用户部门归属。
