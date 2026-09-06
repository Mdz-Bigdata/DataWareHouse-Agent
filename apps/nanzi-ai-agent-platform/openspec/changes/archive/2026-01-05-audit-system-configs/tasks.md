## 1. Database Migration
- [ ] 1.1 **Schema Design**: Create `db-prod/V10-create_system_config_history.sql` with `system_config_history` table. <!-- type: db -->
- [ ] 1.2 **Apply Schema**: Execute the SQL script to update the database. <!-- type: db -->

## 2. Backend Implementation
- [ ] 2.1 **Service Update**: Modify `ConfigService.set_config` and `update_config_value` to accept `changed_by` and `reason` arguments. <!-- type: backend -->
- [ ] 2.2 **History Logic**: Implement logic to fetch `old_value` before update and insert into `system_config_history` after update. <!-- type: backend -->
- [ ] 2.3 **API Endpoint**: Create `GET /api/portal/system/configs/{key}/history` to retrieve history records. <!-- type: backend -->

## 3. Integration
- [ ] 3.1 **Prompt Service**: Update `PromptService.save_prompt` to pass `user.username` to `ConfigService`. <!-- type: backend -->
- [ ] 3.2 **System API**: Update `app/api/portal/endpoints/system.py` to pass `user.username` when updating configs. <!-- type: backend -->

## 4. Testing
- [ ] 4.1 **Unit Test**: Verify that updating a config creates a history record with correct values. <!-- type: test -->
