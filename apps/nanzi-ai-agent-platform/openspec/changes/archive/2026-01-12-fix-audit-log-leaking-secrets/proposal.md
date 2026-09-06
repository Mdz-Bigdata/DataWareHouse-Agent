# Change: Fix Audit Log Leaking Secrets

## Why
Currently, the `AccessLogMiddleware` captures and logs the full request and response bodies. This includes sensitive information such as user passwords (during login) and API keys (during configuration). Storing these in plain text in the `ai_agent_access_logs` table is a critical security vulnerability.

## What Changes
- Implement a `mask_sensitive_data` utility to sanitize JSON payloads and query parameters.
- Update `AuditService` to apply this masking before persisting logs.
- Define a list of sensitive fields (e.g., `password`, `token`, `api_key`, `secret`).

## Impact
- **Security**: Significantly reduces the risk of credential leakage.
- **Code**: 
  - `app/services/audit_service.py`: Add masking logic.
  - `app/utils/security.py` (New or update): Add masking helper.
- **Specs**: New `audit-logging` capability.
