-- ==========================================
-- V17: Create Generic API Tools Table
-- ==========================================

CREATE TABLE IF NOT EXISTS sys_api_tools (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE COMMENT 'Unique tool identifier/name',
    description TEXT COMMENT 'Human readable description of what the tool does',
    method VARCHAR(20) NOT NULL DEFAULT 'GET' COMMENT 'HTTP Method (GET, POST, etc.)',
    url_template TEXT NOT NULL COMMENT 'Target API URL, supports {param} substitution',
    headers TEXT COMMENT 'JSON string of request headers',
    parameter_schema TEXT COMMENT 'JSON schema defining the parameters accepted by this tool',
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add index for active lookups
CREATE INDEX idx_sys_api_tools_active ON sys_api_tools(is_active);
