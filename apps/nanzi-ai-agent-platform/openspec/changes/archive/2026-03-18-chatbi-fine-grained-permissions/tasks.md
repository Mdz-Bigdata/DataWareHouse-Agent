## 1. 数据库与元数据扩展 (Database & Metadata)

- [x] 1.1 编写并执行迁移脚本：为 `ai_agent_users` 表增加 `dept_id` (INT) 和 `org_path` (VARCHAR) 字段。
- [x] 1.2 编写并执行迁移脚本：为 `meta_datasets` 表增加 `enable_data_perm` (BOOLEAN) 和 `row_filter_config` (JSON) 字段。
- [x] 1.3 更新 `app/models/user.py`：同步 `User` 模型的字段定义。
- [x] 1.4 更新 `app/models/metadata.py`：同步 `MetaDataset` 模型的字段定义。

## 2. 用户上下文增强 (User Context Enrichment)

- [x] 2.1 修改 `app/services/auth_service.py`：在验证用户后，加载其 `dept_id` 和 `org_path`。
- [x] 2.2 更新 `app/core/context.py`：在 `AgentContext` 中增加 `user_dimensions` 字典字段。
- [x] 2.3 修改 `app/services/ai/agent_service.py`：在构造 `AgentContext` 时，注入用户的业务维度数据。

## 3. SQL 改写引擎开发 (SQL Rewriter)

- [x] 3.1 环境准备：安装 `sqlglot` 依赖。
- [x] 3.2 实现 `app/services/ai/rewriters/sql_rewriter.py`：利用 `sqlglot` 实现 AST 解析与 `WHERE` 子句注入逻辑。
    - [x] 3.2.1 支持多角色过滤条件的 `OR` 逻辑合并（权限累加原则）。
    - [x] 3.2.2 增加基于元数据的字段存在性检查，避免注入不存在的列导致 SQL 报错。
    - [x] 3.2.3 实现递归处理，确保子查询 (Subquery)、CTE (WITH 语句) 和 UNION 场景均能被正确注入。
- [x] 3.3 单元测试：编写 `tests/utils/test_sql_rewriter.py`，覆盖上述所有复杂场景。
- [x] 3.4 错误处理：在改写失败时抛出自定义异常 `SQLRewriteError`。

## 4. ChatBI 执行器集成 (Executor Integration)

- [x] 4.1 修改 `app/services/ai/tools/data_api.py` 中的 `execute_sql_query`：在执行前调用 `SQLRewriter`。
- [x] 4.2 实现 Trace 记录：在 SQL 改写成功后，将改写详情（命中的策略和注入的片段）存入 `TraceContext`。
- [x] 4.3 安全兜底：如果 `enable_data_perm` 开启但改写引擎报错，强制熔断查询并返回错误。

## 5. 管理 API 与 前端对接 (API & Observability)

- [x] 5.1 实现 `app/api/v1/endpoints/metadata.py`：增加数据集权限配置的查询与更新接口。
- [x] 5.2 实现 `app/api/v1/endpoints/user.py`：增加更新用户部门信息的接口。
- [x] 5.3 前端适配：在 Trace 步骤展示中，新增“数据权限校验”节点。
