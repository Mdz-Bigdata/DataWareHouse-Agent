# tool-management Specification

## Purpose
TBD - created by archiving change implement-generic-api-tools. Update Purpose after archive.
## Requirements
### Requirement: 工具配置 CRUD
系统 MUST 允许用户在管理后台对通用 API 工具进行增删改查。

#### Scenario: 创建新工具
- **Given** 管理员在“工具管理”页面点击“添加工具”。
- **When** 输入工具名称 "search_google"、描述 "搜索互联网"、URL "https://google.com/search?q={query}"、并定义参数 "query" (String, Required)。
- **Then** 系统保存该工具配置到数据库，状态默认为“启用”。
- **And** Agent 系统在下次重载配置或重启时，能够识别并加载该工具。

#### Scenario: 编辑参数定义
- **Given** 存在一个工具 "restart_server"。
- **When** 管理员修改其参数定义，增加一个 "force" (Boolean) 参数。
- **Then** 保存后，LLM 在调用该工具时，会知晓 "force" 参数的存在并尝试在需要时填充它。

#### Scenario: 禁用工具
- **Given** 某个外部 API 暂时不可用。
- **When** 管理员将对应工具的状态切换为“禁用”。
- **Then** Agent 在后续的规划中不再使用该工具，直到重新启用。

#### Scenario: 集成 MCP 工具源
- **Given** 系统配置了多个工具源（Static, Generic API, MCP）。
- **When** 管理员在“工具管理”页面查看列表。
- **Then** 系统必须能够区分并展示这些工具的来源（Source Type）。
- **And** MCP 工具应当自动聚合在所属的 MCP Server 分组下。

### Requirement: 动态参数解析与请求执行
Agent MUST 能够正确解析 LLM 输出的参数，并将其填充到 HTTP 请求中。

#### Scenario: URL 路径参数替换
- **Given** 工具配置 URL 为 `https://api.example.com/users/{user_id}/details`。
- **When** LLM 调用该工具并传入参数 `{"user_id": "1001"}`。
- **Then** 系统实际发出的请求 URL 应为 `https://api.example.com/users/1001/details`。

#### Scenario: 环境变量注入
- **Given** 工具 Headers 配置为 `{"Authorization": "Bearer ${MY_API_KEY}"}`。
- **And** 系统环境变量中存在 `MY_API_KEY=secret_123`。
- **When** 执行该工具请求时。
- **Then** 请求头中应包含 `Authorization: Bearer secret_123`。

#### Scenario: 执行 MCP 工具调用
- **Given** 一个以 `mcp:` 前缀注册的工具（如 `mcp:github:create_issue`）。
- **When** LLM 发起调用请求。
- **Then** 系统必须通过 MCP SSE客户端将请求路由至对应的 MCP Server Endpoint。
- **AND** 结果必须按标准 MCP 响应格式进行解析并回传。
