# Proposal: System Configuration Audit (系统配置审计)

## Why
目前 `system_configs` 表存储了系统的核心配置（如 LLM 参数、系统级 Prompt），但采用的是覆盖更新模式。
当配置被错误修改或需要回溯历史版本时，缺乏审计日志（Audit Log）来追踪“谁、在什么时候、修改了什么”。
这对生产环境的稳定性和安全性构成风险。

## What Changes
1.  **Database**: 新增 `system_config_history` 表，用于记录每一次配置变更的快照。
2.  **Backend**:
    - 更新 `ConfigService`，在执行写操作（Create/Update）时自动记录历史。
    - 记录内容包括：`old_value`, `new_value`, `changed_by`, `change_reason`。
    - 新增 API `GET /api/portal/system/configs/{key}/history` 用于查询历史。
3.  **Integration**:
    - `PromptService` 保存系统 Prompt 时，透传当前操作人信息。
    - `SystemConfig` 管理接口保存配置时，透传当前操作人信息。

## Impact
- **Database**: 新增一张表。
- **Backend**: `ConfigService` 接口签名变更，需增加 `changed_by` 参数。
- **Frontend**: 系统配置页面和 Prompt Studio 未来可展示历史记录（本期暂不涉及前端历史列表 UI 开发，仅后端就绪）。

## Affected Specs
- `specs/system-config`
