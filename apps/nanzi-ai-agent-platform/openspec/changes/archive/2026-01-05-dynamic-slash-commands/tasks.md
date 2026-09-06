## 1. Database
- [ ] 1.1 **Schema**: Create `db-prod/V11-create_slash_commands.sql`. <!-- type: db -->
- [ ] 1.2 **Apply**: Apply the migration. <!-- type: db -->

## 2. Backend
- [ ] 2.1 **Service**: Create `app/services/slash_command_service.py`. <!-- type: backend -->
- [ ] 2.2 **API**: Create `app/api/portal/endpoints/slash_commands.py` with CRUD. <!-- type: backend -->
- [ ] 2.3 **Router**: Register in `app/api/portal/api.py`. <!-- type: backend -->

## 3. Frontend
- [ ] 3.1 **Fetch Logic**: Update `AgentDebug.vue` to fetch commands on mount. <!-- type: frontend -->
- [ ] 3.2 **Manage UI**: Add a "Manage" button and a Modal for CRUD operations in `AgentDebug.vue`. <!-- type: frontend -->
