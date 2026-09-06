# 规范：意图识别引擎 (Intent Recognition Engine)

## ADDED Requirements

### Requirement: 输出结构化校验 (Structured Output)
意图引擎 **MUST** 通过 Prompt 工程或工具强迫 LLM 返回有效的 JSON 格式数据。

#### Scenario: 自动解析结果
业务层调用意图识别接口后，返回的结果必须可以直接解析为 Pydantic 模型。

### Requirement: 核心意图覆盖 (Core Intents)
意图引擎初始化 **MUST** 至少包含三种核心意图：`DATA_QUERY` (查数)、`KNOWLEDGE_BASE` (问答)、`GENERAL` (闲聊)。

#### Scenario: 精准分配业务逻辑
用户输入“统计上月能耗”时，引擎应能准确返回 `DATA_QUERY` 类别。
