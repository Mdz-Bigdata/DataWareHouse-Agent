## Why
目前 ChatBI 仅支持大粒度的数据集（表级）访问控制。随着业务深入，亟需实现：
1. **行级数据隔离**：确保不同地域/部门的用户只能查询其授权范围内的数据。
2. **字段级安全保障**：对敏感字段（如：薪资、手机号）进行动态脱敏或完全隐藏。
3. **分层权限管理**：支持“用户 > 角色 > 默认”的优先级配置，满足复杂的企业级审计要求。
4. **过程透明化**：在前端执行链路中展示权限校验过程，增强系统的合规性感知与可调试性。

## What Changes
*   **数据集元数据增强**：在 `MetaDataset` 增加权限开关 `enable_data_perm` 和层级化配置 `row_filter_config`（支持 User/Role 维度）。
*   **用户上下文注入**：增强 `AgentContext`，在对话初始化阶段自动加载并传递用户的业务维度（如 `region_code`, `dept_id`）。
*   **SQL 执行引擎改写**：拦截 `execute_sql_query` 工具调用，利用 `sqlglot` 动态改写 SQL AST，注入 `WHERE` 约束和 `SELECT` 掩码。
*   **执行链路追踪 (Tracing)**：在 `DataQueryExecutor` 的执行痕迹中记录权限校验详情，并将“数据权限注入”作为独立步骤反馈给前端显示。
*   **配置管理接口**：提供标准接口，允许管理员为特定用户或角色配置专属的数据过滤规则。

## Capabilities

### New Capabilities
- **data-row-security**: 实现基于 SQL 注入的行级数据范围控制。
- **data-column-masking**: 实现针对敏感字段的动态掩码与字段级可见性控制。
- **user-context-enrichment**: 构建一套能够获取并传递用户业务维度（Dimensions）的上下文机制。
- **permission-observability**: 实现权限校验过程的可视化追踪逻辑。

### Modified Capabilities
- **chatbi**: 升级 ChatBI 执行流程，在 SQL 执行前强制引入权限重写环节并记录 Trace 日志。

## Impact
*   **Metadata Service**: 更新 `meta_datasets` 表结构，并提供策略配置接口。
*   **Agent Service / Executor**: 在 `DataQueryExecutor` 中引入 `SQLRewriter` 组件。
*   **Trace Service**: 需要记录并存储 SQL 改写前后的差异及应用的策略详情。
*   **Frontend**: 在 Trace 步骤面板中新增“数据权限校验”节点。
