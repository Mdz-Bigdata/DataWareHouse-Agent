-- ==========================================
-- V43: Create MCP (Model Context Protocol) Management Tables
-- ==========================================

-- 1. MCP Servers Table: Manage SSE Connections
CREATE TABLE IF NOT EXISTS sys_mcp_servers (
    id CHAR(36) PRIMARY KEY,
    server_name VARCHAR(100) NOT NULL UNIQUE COMMENT 'Unique name for the MCP server',
    sse_url TEXT NOT NULL COMMENT 'SSE Handshake URL (e.g., http://host:port/sse)',
    auth_headers TEXT COMMENT 'JSON string of global auth headers/secrets',
    enabled_status TINYINT DEFAULT 0 COMMENT '0: Offline/Disabled, 1: Online/Enabled',
    last_sync_at DATETIME COMMENT 'Last time tools were synchronized',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. MCP Tools Cache Table: Snapshot of discovered tools
CREATE TABLE IF NOT EXISTS sys_mcp_tool_cache (
    id CHAR(36) PRIMARY KEY,
    server_id CHAR(36) NOT NULL COMMENT 'Link to sys_mcp_servers',
    tool_name VARCHAR(255) NOT NULL COMMENT 'Tool full name (e.g., github:create_issue)',
    tool_description TEXT COMMENT 'Tool description from MCP Server',
    parameter_schema TEXT COMMENT 'JSON Schema of parameters',
    is_published BOOLEAN DEFAULT FALSE COMMENT 'Whether this tool is visible to agents',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY idx_mcp_tool_server_name (server_id, tool_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add index for published tool lookups
CREATE INDEX idx_mcp_tool_published ON sys_mcp_tool_cache(is_published);