# v1-chat 规范增量 (Spec Delta)

## 新增需求 (ADDED Requirements)

### Requirement: 对话历史查询 (Dialogue History Retrieval)
系统 **MUST** 提供查询对话历史的 API，支持分页、关键词搜索、时间范围过滤及 Agent 筛选。

#### Scenario: 用户查看历史记录
用户在前端点击“历史记录”，请求 API 获取最近的 20 条对话，包含 `agent_version` 信息，用于区分不同版本的回答。

#### Scenario: 关键词搜索
用户搜索“上周的 PUE 数据”，API 应对 `query` (用户提问) 和 `summary` (AI 回答) 字段进行模糊匹配，并返回匹配结果。

### Requirement: 版本溯源 (Version Tracking)
每一次对话记录 **MUST** 包含生成该回复时所使用的 Agent 版本号 (`agent_version`)。

#### Scenario: 版本对比
用户发现今天的回答与昨天不同，查看历史记录发现昨天的 `agent_version` 是 "v1.0"，今天是 "v1.1"，从而确认是 Agent 更新导致的差异。