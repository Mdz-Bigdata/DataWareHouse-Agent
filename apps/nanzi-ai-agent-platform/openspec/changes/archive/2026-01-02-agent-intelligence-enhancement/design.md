# 设计文档：智能体自愈与状态可视化

## 1. 详细设计

### 1.1 自愈逻辑状态机
现在的逻辑是：`Thought -> Tool Call -> (Error) -> Break -> Apology`。
优化后的逻辑是：`Thought -> Tool Call -> (Error) -> Feed Error Back -> New Thought -> Corrected Tool Call -> Success/Final Step`。

**自愈提示词模板**：
```text
The previous tool execution for '{tool_name}' failed with the following error:
---
{error_message}
---
Please analyze the error. If it's a schema issue (e.g., column not found), consider calling 'get_dataset_schema' again. If it's a syntax error, fix your SQL. You have {remaining_steps} attempts left to solve this.
```

### 1.2 上下文提取协议
后端在流式输出中实时推送上下文状态：
```json
{
  "type": "context",
  "data": {
    "room": "华东一号机房",
    "time_range": "2023-12-01 to 2023-12-31",
    "target_metric": "PUE"
  }
}
```

### 1.3 前端 UI 组件
- **组件名**：`AgentContextStack.vue`
- **显示位置**：调试页/对话页右侧栏。
- **数据结构**：
  ```ts
  const currentContext = ref({
    entities: [
      { key: '机房', value: '华东一号', icon: 'Home' },
      { key: '时间', value: '最近30天', icon: 'Clock' }
    ]
  })
  ```

## 2. 接口变动
- `POST /api/v1/chat/completions` (Stream):
  - 新增 `type: "context"` 类型的 SSE 事件。

## 3. 安全性考虑
- 自愈过程中的中间报错不应直接暴露给最终用户（可在调试日志中显示），Agent 最终应给出一个合成后的优雅回复。
