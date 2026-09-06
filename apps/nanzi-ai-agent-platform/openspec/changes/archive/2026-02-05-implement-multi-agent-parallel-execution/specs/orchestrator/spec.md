# 编排服务 (Orchestration Service)

## MODIFIED Requirements

### Requirement: 多智能体并行执行
AgentService **MUST** 能够根据路由结果并行执行多个智能体任务。

#### Scenario: 触发并行执行
- **Given** Router 返回了 `chat-bi` 和 `knowledge-base` 两个智能体。
- **When** AgentService 开始处理请求。
- **Then** 它应该创建两个独立的 Executor 实例。
- **And** 使用 `asyncio.gather` (或类似机制) 并行启动这两个 Executor。
- **And** 两个 Executor 的日志应通过 SSE 流实时发送给客户端。

### Requirement: 结果聚合 (Synthesis)
当多个智能体执行完毕后，系统 **MUST** 调用聚合模型生成最终回复。

#### Scenario: 聚合多方结果
- **Given** 并行执行已完成。
- **And** `chat-bi` 返回了 "服务器数量为 120"。
- **And** `knowledge-base` 返回了 "维护周期为每季度"。
- **When** 进入聚合阶段。
- **Then** 系统应构造一个包含上述两个结果的 Prompt。
- **And** 调用 LLM 生成如 "上海机房有 120 台服务器，且需每季度维护一次" 的最终回答。
- **And** 最终回答通过 SSE 流式推送到前端。

### Requirement: 异常隔离
并行执行中，单个智能体的失败 **MUST NOT** 导致整体请求失败。

#### Scenario: 部分智能体失败
- **Given** 并行执行中，`chat-bi` 成功返回数据，但 `knowledge-base` 超时或报错。
- **When** 聚合阶段开始。
- **Then** 聚合 Prompt 中应包含 `chat-bi` 的结果。
- **And** 对于 `knowledge-base`，应标记为 "无法获取信息" 或忽略，而不是抛出 500 错误。
- **And** 最终回复应基于可用信息生成。
