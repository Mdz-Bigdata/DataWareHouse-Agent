## ADDED Requirements

### Requirement: Global MCP Server Registry
系统必须提供管理界面，允许管理员配置全局 MCP 服务端。

#### Scenario: Registering a new MCP Server
- **WHEN** Admin enters a name and SSE URL in the MCP Management Tab
- **THEN** the system MUST validate the URL format
- **AND** create a record in `sys_mcp_servers` table

### Requirement: Tool Metadata Synchronization
系统必须支持从已注册的 MCP Server 同步工具列表及其参数定义的快照。

#### Scenario: Manual Sync of Tools
- **WHEN** Admin clicks the "Sync" button for an MCP Server
- **THEN** the system MUST call the Server's `list_tools` endpoint
- **AND** upsert the returned tools into `sys_mcp_tool_cache`
- **AND** update the `last_sync_at` timestamp

### Requirement: Agent-Level Configuration Overrides
在智能体配置层面，系统必须允许覆盖全局定义的 MCP 认证信息。

#### Scenario: Configuring Tool-Specific Secrets
- **WHEN** User selects an MCP tool for an agent
- **THEN** the UI MUST provide fields to input override headers or secrets
- **AND** these values MUST be stored securely (encrypted) associated with the agent's tool config
