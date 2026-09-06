# Metrics Management Spec Delta

## ADDED Requirements

### Requirement: 智能体维度监控 (Agent-Level Monitoring)
Dashboard **MUST** 提供基于智能体维度的聚合监控数据。

#### Scenario: 性能分析
管理员登录 Dashboard，查看“智能体性能列表”，发现“ChatBI”的平均耗时为 5000ms，而“闲聊助手”仅为 500ms，从而决定优化 ChatBI 的 SQL 生成逻辑。

#### Scenario: 热门应用识别
通过“智能体调用占比”饼图，管理员发现 80% 的流量都集中在“知识库助手”，决定为其分配更多资源或优先升级模型。
