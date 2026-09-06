# Tasks

## Backend (API & Logic)

- [ ] <!-- id: 1 --> **Permissions**: Update `AgentPermissionService` to enforce hybrid access control (System+Perm vs Own+Enabled).
- [ ] <!-- id: 2 --> **Agent Management**: Update `get_agent_list_for_assignment` to filter only `is_system=True` AND `is_enabled=True`.
- [ ] <!-- id: 3 --> **Orchestration**: Update `RouterService` candidate selection to include ONLY `is_system=True` AND `is_enabled=True` agents.
- [ ] <!-- id: 4 --> **Chat API**: Update `get_mention_list` (or equivalent) to return the union of System+Perm and Own+Enabled agents.
- [ ] <!-- id: 5 --> **Chat API**: Update `chat` endpoint to accept an explicit `target_agent_id` (or `routing_mode` context) to support Expert Mode bypassing logic.

## Frontend (UI/UX)

- [ ] <!-- id: 6 --> **Settings**: Add "Routing Mode" (Auto/Expert) toggle and "Default Expert Agent" selector in the Settings dialog.
- [ ] <!-- id: 7 --> **Chat UI**: Implement the "Expert Mode" banner above the input box (visible only when Expert Mode is active).
- [ ] <!-- id: 8 --> **Chat Logic**: Ensure the frontend passes the correct `target_agent_id` (or appropriate flag) when in Expert Mode.
- [ ] <!-- id: 9 --> **Permission UI**: Ensure the Admin Permission Assignment list reflects the new filtering rules (System+Enabled only).
