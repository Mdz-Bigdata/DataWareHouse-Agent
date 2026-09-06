## 1. 数据库变更 (Database Changes)

- [x] 1.1 **Schema**: 创建 SQL 脚本 `db-prod/V43-create_mcp_tables.sql`，定义 `sys_mcp_servers` 和 `sys_mcp_tool_cache` 表结构。 <!-- id: db-mcp -->
- [x] 1.2 **Data**: 在 `V43` 脚本中预置一些常用的 MCP Server 示例（如本地测试服务）。 <!-- id: db-seed -->

## 2. 核心服务层 (Core Services)

- [x] 2.1 **Dependencies**: 在 `requirements.txt` 中添加 `mcp` SDK 或确保 `httpx` 版本兼容 SSE。 <!-- id: dep-mcp -->
- [x] 2.2 **Model**: 在 `app/models/mcp.py` 中定义 SQLAlchemy 模型 `McpServer` 和 `McpToolCache`（含 `is_published` 字段）。 <!-- id: model-mcp -->
- [x] 2.3 **Service (SSE Client)**: 创建 `app/services/ai/tools/mcp_client.py`，实现 `McpSseClient` 类，采用“懒加载”策略管理连接和 Endpoint 缓存。 <!-- id: svc-mcp-client -->
- [x] 2.4 **Service (Sync & Publish)**: 实现工具同步逻辑，并将同步后的工具默认设为“未发布”状态；增加发布/下线工具的逻辑。 <!-- id: svc-mcp-sync -->
- [x] 2.5 **Background Task**: 实现闲置连接释放逻辑（5 分钟未活动自动断开）。 <!-- id: svc-mcp-idle -->

## 3. 工具管理与注册 (Tool Registry)

- [x] 3.1 **Registry Update**: 修改 `app/services/ai/tools/registry.py`，在 `get_tool` 方法中增加对 `mcp:` 前缀的处理逻辑，且仅允许加载 `is_published=True` 的工具。 <!-- id: registry-mcp -->
- [x] 3.2 **Factory Update**: 创建 `app/services/ai/tools/mcp_factory.py`，实现 `McpToolFactory`，用于将缓存的 MCP 工具元数据转换为 LangChain `StructuredTool`。 <!-- id: factory-mcp -->
- [x] 3.3 **Error Handling**: 在 `McpSseTool` 执行逻辑中，实现错误透传机制，确保用户能看到具体的不可用原因。 <!-- id: tool-error-handling -->

## 4. API 接口 (API Endpoints)

- [x] 4.1 **Server Management**: 创建 `app/api/portal/endpoints/mcp.py`，实现 MCP Server 的增删改查、同步及工具发布管理接口。 <!-- id: api-mcp-server -->
- [x] 4.2 **Tool Discovery**: 在 `app/api/portal/endpoints/tools.py` 中增加接口，仅返回已发布的 MCP 工具供智能体配置使用。 <!-- id: api-mcp-discovery -->

## 5. 前端界面 (Frontend)

- [x] 5.1 **MCP Config Tab**: 在系统设置页面实现 MCP 管理 Tab，包含服务端列表、同步操作以及工具发布开关。 <!-- id: fe-mcp-admin -->
- [x] 5.2 **Agent Tool Tab**: 在智能体编辑页面，将工具选择区拆分为两个 Tab：“系统工具”和“MCP 工具”，支持按分类勾选。 <!-- id: fe-agent-tools -->

## 6. 测试与验证 (Verification)

- [x] 6.1 **Unit Test**: 为 `McpSseClient` 编写单元测试，Mock SSE 响应流进行测试。 <!-- id: test-mcp-client -->
- [x] 6.2 **Integration Test**: 编写集成测试，模拟一个简单的 MCP Server，验证从注册、同步到调用的全流程。 <!-- id: test-mcp-integration -->
- [x] 6.3 **Manual Verification**: 启动系统，配置真实的 MCP Server（如 GitHub MCP），通过 Chat 界面验证智能体能否成功调用工具。 <!-- id: verify-manual -->
