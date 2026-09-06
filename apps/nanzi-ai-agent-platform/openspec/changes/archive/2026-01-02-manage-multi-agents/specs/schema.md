# Database Schema Specification: Multi-Agent Management

## New Tables

### 1. `ai_agents`
Stores the high-level metadata of an AI agent.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `VARCHAR(36)` | Primary Key (UUID) |
| `name` | `VARCHAR(100)` | Agent unique identifier (e.g. 'chat-bi') |
| `display_name` | `VARCHAR(100)` | Human-readable name (e.g. '数据智能助手') |
| `description` | `TEXT` | Brief description of the agent's purpose |
| `avatar_url` | `VARCHAR(255)` | Optional icon or avatar |
| `is_system` | `BOOLEAN` | If true, cannot be deleted (Default agents) |
| `created_at` | `DATETIME` | Creation timestamp |
| `updated_at` | `DATETIME` | Last update timestamp |

### 2. `ai_agent_versions`
Stores specific versions of an agent's configuration.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `VARCHAR(36)` | Primary Key (UUID) |
| `agent_id` | `VARCHAR(36)` | Foreign Key to `ai_agents` |
| `version_number` | `INT` | Manual or auto-incremented version |
| `model_name` | `VARCHAR(100)` | Model override (e.g. 'deepseek-chat') |
| `temperature` | `FLOAT` | Temperature override |
| `system_prompt` | `TEXT` | The core "persona" prompt |
| `tools` | `JSON` | List of tool names (e.g. `["get_dataset_schema", "execute_sql_query"]`) |
| `status` | `VARCHAR(20)` | `DRAFT`, `PUBLISHED`, `ARCHIVED` |
| `created_at` | `DATETIME` | Creation timestamp |
| `comment` | `VARCHAR(255)` | Change log for this version |

## Migration Plan

1. **V5-create_agent_management.sql**:
   - Create the two tables.
   - Insert the current "Default Agent" (ChatBI) as `is_system=true`.
   - Insert its current prompt and tool list as Version 1 status `PUBLISHED`.

2. **Compatibility Strategy**:
   - `AgentService` will have an optional `agent_id` parameter.
   - If `agent_id` is null, it will query for the `ai_agents` where `name = 'chat-bi'` and its latest `PUBLISHED` version.
   - This ensures existing API calls continue to work without modification.
