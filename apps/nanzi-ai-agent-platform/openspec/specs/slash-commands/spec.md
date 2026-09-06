# slash-commands Specification

## Purpose
TBD - created by archiving change dynamic-slash-commands. Update Purpose after archive.
## Requirements
### Requirement: Dynamic Slash Commands
The system MUST support dynamic management of slash commands (shortcuts) displayed in the debug interface.

#### Scenario: Add new command
- **WHEN** user adds a new command "Test API" with content "Check API status"
- **THEN** it appears in the shortcut list immediately.

### Requirement: Persistence
Commands MUST be persisted in the database and shared across sessions (or per user if designed so, currently global for simplicity based on request).

#### Scenario: List commands
- **WHEN** Agent Debug page loads
- **THEN** system fetches active commands from the database.

