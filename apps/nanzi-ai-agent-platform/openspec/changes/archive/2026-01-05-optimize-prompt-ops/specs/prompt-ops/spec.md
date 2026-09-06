## ADDED Requirements
### Requirement: 统一 Prompt 管理 (Unified Prompt Management)
系统 **MUST** 提供一个统一的接口，用于列出、查看和编辑来自异构源（`system_configs` 和 `ai_agent_versions`）的 Prompt。

#### Scenario: 列出所有 Prompt
- **WHEN** 用户访问 Prompt Studio 页面
- **THEN** 系统显示分类列表：“系统 Prompts”（路由、意图）和“智能体 Prompts”（ChatBI, 知识库等）。

### Requirement: Prompt 变量注入测试 (Prompt Variable Injection Testing)
系统 **MUST** 允许用户为 Prompt 变量输入模拟值（Mock Values），并针对 LLM 执行该 Prompt。

#### Scenario: 测试路由 Prompt
- **WHEN** 用户选择“路由 Prompt”并输入模拟的 `{history_context}`
- **AND** 点击“运行测试”
- **THEN** 系统执行变量替换，调用配置的 LLM，并返回原始输出结果。

### Requirement: 在线更新 (Online Update)
系统 **MUST** 允许用户将修改后的 Prompt 直接保存到持久化存储（数据库）中。

#### Scenario: 更新意图 Prompt
- **WHEN** 用户修改“意图识别 Prompt”并点击保存
- **THEN** `system_configs` 表中的对应值被立即更新。