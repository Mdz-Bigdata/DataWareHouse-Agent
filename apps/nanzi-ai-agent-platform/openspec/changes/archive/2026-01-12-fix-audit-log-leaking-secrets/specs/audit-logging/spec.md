## ADDED Requirements
### Requirement: Access Log Sensitive Data Masking
The system MUST mask sensitive fields in the access logs (request body, response body, and query parameters) before storage.

#### Scenario: Login Request Masking
- **WHEN** a user logs in with a JSON body containing `password`
- **THEN** the stored log entry shows `password: "******"`
- **AND** the original password is NOT stored

#### Scenario: API Key Masking
- **WHEN** a configuration update contains `api_key` or `secret`
- **THEN** the stored log entry shows `api_key: "******"`

#### Scenario: Nested JSON Masking
- **WHEN** a request contains nested sensitive data (e.g. `{"user": {"password": "123"}}`)
- **THEN** the system recursively masks the sensitive field
