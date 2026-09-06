## 1. Implementation
- [ ] 1.1 **Utility**: Create `app/utils/masking.py` with `mask_sensitive_data(data: dict | str) -> dict | str` function. Handles recursive masking for keys like `password`, `token`, `api_key`, `secret`.
- [ ] 1.2 **Service Integration**: Update `AuditService.log_request_data` in `app/services/audit_service.py` to call `mask_sensitive_data` on `request_params` and `response_body`.
- [ ] 1.3 **Testing**: Add unit tests in `tests/core/test_masking.py` to verify various JSON structures and edge cases (malformed JSON).
- [ ] 1.4 **Verification**: Update `tests/api/portal/test_auth.py` to verify that login logs do not contain the actual password.
