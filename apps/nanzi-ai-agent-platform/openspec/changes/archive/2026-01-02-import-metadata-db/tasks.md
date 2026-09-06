# Tasks: Import Metadata from Database

- [ ] Backend Implementation <!-- id: 1 -->
    - [ ] Create `DBImportService` for multi-database connectivity <!-- id: 2 -->
    - [ ] Add API endpoints for connection testing and DDL extraction <!-- id: 3 -->
    - [ ] Implement MySQL and ClickHouse DDL fetchers <!-- id: 4 -->
- [ ] Frontend Implementation <!-- id: 5 -->
    - [ ] Create `DatabaseImportModal.vue` component <!-- id: 6 -->
    - [ ] Update `SmartImportWizard.vue` to integrate the new modal <!-- id: 7 -->
    - [ ] Add API methods to `frontend/src/api/metadata.ts` <!-- id: 8 -->
- [ ] Verification <!-- id: 9 -->
    - [ ] Test MySQL connection and DDL loading <!-- id: 10 -->
    - [ ] Test ClickHouse connection and DDL loading <!-- id: 11 -->
    - [ ] Verify DDL successfully populates the import textarea <!-- id: 12 -->
