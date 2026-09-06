# Proposal: Multi-Agent Management System (MAMS)

## Background
Currently, the system operates as a single, hardcoded agent (`AgentService`) with fixed tools and one intent capability.
To support diverse business scenarios (e.g., "SQL Expert", "SOP Assistant", "Customer Support"), we need a flexible architecture to create, manage, and version multiple agents.

## Goals
1.  **Multi-Agent Architecture**: Support defining multiple named agents with distinct personalities (System Prompts) and toolsets.
2.  **Versioning**: Allow agents to have multiple versions (Draft, Published, Archived) to safely iterate on prompts/logic.
3.  **Management UI**: Provide an interface to create, configure, and debug agents.
4.  **Dynamic Routing**: Update the API to route chat requests to specific `agent_id`.

## Schema Design (Preliminary)

### `ai_agents`
- `id`: UUID
- `name`: string
- `description`: string
- `avatar_url`: string
- `created_at`: timestamp
- `updated_at`: timestamp

### `ai_agent_versions`
- `id`: UUID
- `agent_id`: UUID (FK)
- `version_number`: int (1, 2, 3...)
- `model`: string (e.g., "gpt-4o")
- `system_prompt`: text
- `tools`: json (List of tool names allowed)
- `status`: enum (DRAFT, PUBLISHED, ARCHIVED)

## API Changes
- `POST /api/v1/agents`: Create Agent
- `POST /api/v1/agents/{id}/versions`: Create Version
- `POST /api/v1/agents/{id}/chat`: Chat with specific agent (uses latest PUBLISHED version by default)

## Plan
1. Design DB Schema & Migration.
2. Implement backend CRUD.
3. Update `AgentService` to load configuration from DB.
4. Build Frontend Manager.
