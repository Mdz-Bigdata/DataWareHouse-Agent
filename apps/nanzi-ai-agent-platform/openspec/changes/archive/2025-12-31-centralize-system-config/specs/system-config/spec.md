## ADDED Requirements

### Requirement: 动态系统配置 (Dynamic System Configuration)
系统必须 (MUST) 支持通过系统管理界面管理关键的基础设施配置，而无需重启服务。

#### Scenario: 管理员更新 LLM 模型
- **WHEN** 管理员在系统配置中将 "LLM Model Name" 修改为 "gpt-4-turbo" 并保存
- **THEN** 系统验证输入有效性
- **AND** 系统更新数据库并刷新 Redis 缓存
- **AND** 后续的对话请求立即使用 "gpt-4-turbo" 模型
- **AND** API Key 如果未修改，在 UI 上保持掩码状态。

#### Scenario: 环境变量兜底 (Fallback to Environment Variables)
- **WHEN** 数据库配置中不存在特定的配置项（例如 `LLM_API_KEY`）
- **THEN** 系统回退使用运行环境 (`.env`) 中定义的值
- **AND** 如果两者都未找到（对于必填项），记录警告日志。

### Requirement: 配置安全性 (Configuration Security)
UI 和 API 必须 (MUST) 保护敏感配置值（如 API Key）。

#### Scenario: 查看敏感配置
- **WHEN** 管理员请求当前配置列表
- **THEN** 标记为 "Secret" 的字段（如 API Keys）返回部分掩码的值（例如 `sk-****1234`）或完全隐藏
- **AND** 除非明确必要且经过授权，否则永远不应将真实值以明文形式发送到前端。

### Requirement: 配置范围 (Configuration Scope)
以下配置应 (SHALL) 可以通过 UI 进行管理：
- **LLM 网关**: Base URL, API Key, Model Name, Temperature
- **数据查询 API**: Endpoint URL, API Key

#### Scenario: 编辑数据源 API
- **WHEN** 管理员更新 "External SQL API URL"
- **THEN** `execute_sql_query` 工具立即将流量引导至新的端点。
