# 系统配置需求变更 (Spec Delta)

## ADDED Requirements

### Requirement: 模型注册管理
系统 **MUST** 提供统一的 AI 模型注册表，允许管理员动态管理不同提供商的模型及其对应的 API 凭据。

#### Scenario: 管理员添加并使用新模型
- **WHEN** 管理员在“模型管理”标签页中添加了一个名为“DeepSeek-V3”的新模型，并配置了对应的 API Key 和 Base URL
- **THEN** 该模型应该立即出现在可用的模型列表中
- **AND** 其他系统组件（如智能体配置）可以引用此模型并使用其专属凭据

### Requirement: 动态模型参数配置
系统参数配置中的默认模型设置 **SHALL** 与模型注册表同步。

#### Scenario: 默认模型下拉选择
- **WHEN** 管理员在“参数配置”中编辑 `llm_model_name`
- **THEN** 系统应展示一个下拉列表，包含所有在注册表中处于“启用”状态的 LLM 类型模型
- **AND** 选中保存后，系统的全局默认模型将切换为该模型

### Requirement: 执行历史模型追踪
系统在记录智能体执行历史时，**MUST** 保留当时使用的模型快照信息。

#### Scenario: 审计日志记录模型信息
- **WHEN** 智能体执行完成并保存历史记录
- **THEN** `agent_execution_history` 中必须记录 `model_id` (模型标识) 和 `model_config_id` (引用 ID)
- **AND** 审计界面应能展示该次对话具体使用的模型名称
