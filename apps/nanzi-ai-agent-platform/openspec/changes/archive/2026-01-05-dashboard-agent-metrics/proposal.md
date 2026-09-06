# 提案：Dashboard 智能体性能监控视图

## 背景
目前的 Dashboard 主要关注系统级指标（总 API 调用、在线用户等），缺乏针对“智能体 (Agent)”维度的业务监控。随着智能体数量增加，管理员需要了解每个智能体（如 ChatBI、知识库助手）的实际表现。

## 目标
在 Dashboard 中增加 **"智能体性能概览"** 模块，展示：
1. **调用分布**：哪些智能体被使用得最多？
2. **性能对比**：各智能体的平均响应耗时。
3. **健康度**：各智能体的对话成功率。

## 解决方案
### 后端
- 修改 `GET /api/portal/dashboard/agent-stats` 接口（或新增），基于 `ai_agent_execution_history` 表聚合数据。
- 聚合维度：`agent_id`。
- 指标：`count(*)` (调用量), `avg(execution_time_ms)` (耗时), `sum(case when status='success'...)` (成功率)。

### 前端
- 在 `Dashboard.vue` 底部增加一个新的 Section。
- 左侧：**智能体调用占比** (饼图/环形图)。
- 右侧：**智能体性能列表** (表格，包含名称、版本、调用次数、平均耗时、成功率)。

## 风险
- 数据量大时聚合查询可能变慢，建议对 dashboard 接口增加短暂缓存 (Redis 1-5分钟)。
