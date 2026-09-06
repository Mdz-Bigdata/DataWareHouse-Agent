## ADDED Requirements

### Requirement: MCP SSE Protocol Implementation
系统必须支持通过 HTTP SSE 协议连接外部 MCP Server，并能够处理 `endpoint` 事件以建立正式的命令执行通道。

#### Scenario: Successful Tool Discovery
- **WHEN** McpClientService initializes a connection to an SSE URL
- **THEN** it MUST listen for the `endpoint` event from the server
- **AND** it SHALL be able to call `list_tools` on the discovered endpoint

#### Scenario: Executing an MCP Tool
- **WHEN** an agent calls an MCP-branded tool (e.g., `github:create_issue`)
- **THEN** McpClientService MUST send a POST request with JSON payload to the MCP endpoint
- **AND** it MUST return the standard MCP tool response back to the agent

### Requirement: SSE Connection Lifecycle
系统 SHALL 自动管理 SSE 连接的生命周期，包括心跳检查和断线重连。

#### Scenario: Handling Connection Failure
- **WHEN** an MCP Server endpoint becomes unreachable during a tool call
- **THEN** the system MUST attempt to re-establish the SSE handshake
- **AND** retry the tool call once before reporting an error
