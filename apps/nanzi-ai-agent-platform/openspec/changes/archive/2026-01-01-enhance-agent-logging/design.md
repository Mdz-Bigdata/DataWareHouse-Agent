# Design: 智能体日志系统设计

## 1. 数据库设计 (Database Schema)

我们需要一张新表来存储执行链路的详细步骤。这就好比是程序的 "Stack Trace"，但它是业务层面的。

```sql
CREATE TABLE ai_agent_execution_traces (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    trace_id VARCHAR(64) NOT NULL COMMENT '关联 Access Log 的 Trace ID',
    step_number INT NOT NULL COMMENT '步骤序号，从 1 开始',
    event_type VARCHAR(20) NOT NULL COMMENT '事件类型: thought, tool_call, tool_result, final_answer, error',
    agent_name VARCHAR(50) COMMENT '执行该步骤的 Agent 名称',
    tool_name VARCHAR(100) COMMENT '工具名称 (仅 tool_call/tool_result)',
    tool_input JSON COMMENT '工具入参',
    tool_output JSON COMMENT '工具出参',
    execution_time_ms FLOAT COMMENT '该步骤耗时 (毫秒)',
    status VARCHAR(20) DEFAULT 'success' COMMENT '状态: success, error',
    error_message TEXT COMMENT '错误详情',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trace_id (trace_id)
) COMMENT '智能体执行链路日志表';
```

## 2. 数据流转 (Data Flow)

### 2.1 日志采集 (Collection)
在 `AgentService` 的生命周期中，维护一个 `List[AgentExecutionStep]`。
- 当 LLM 输出思考过程时 -> 记录 `thought` 事件。
- 当调用 `tool.ainvoke` 前 -> 记录 `tool_call` 事件。
- 当工具返回结果后 -> 记录 `tool_result` 事件，并计算耗时。
- 当发生异常时 -> 记录 `error` 事件。

### 2.2 持久化 (Persistence)
由于流式响应 (`yield`) 是异步的，我们不能阻塞主线程去写数据库。
- **方案 A (简单)**: 在 `finally` 块中，一次性批量插入所有步骤到 MySQL。
- **方案 B (健壮)**: 使用 `BackgroundTasks` 或消息队列。考虑到 V1 阶段，方案 A 足够，只要注意异常捕获不影响主流程。

### 2.3 前端展示 (Visualization)
- **入口**: 对话气泡右下角的小图标。
- **展现形式**: 
    - **时间轴 (Timeline)**: 垂直排列每一步。
    - **折叠面板**: 默认折叠大段 JSON，点击展开。
    - **状态标识**: 绿色圆点表示成功，红色表示失败。

## 3. API 设计

`GET /api/v1/chat/logs/{trace_id}`

**Response:**
```json
{
  "code": 200,
  "data": {
    "trace_id": "...",
    "total_steps": 5,
    "steps": [
      {
        "step": 1,
        "type": "thought",
        "content": "用户想要查询 PUE..."
      },
      {
        "step": 2,
        "type": "tool_call",
        "tool": "get_dataset_schema",
        "input": {"keywords": "PUE"}
      },
      ...
    ]
  }
}
```