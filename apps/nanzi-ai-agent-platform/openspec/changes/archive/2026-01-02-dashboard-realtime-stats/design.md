# Design: Dashboard 智能体统计

## 1. 核心指标定义

我们将从 `ai_agent_execution_traces` 表中提取以下维度的数据：

- **工具健康度**: `SUM(status='success') / COUNT(*)`
- **负载分布**: `GROUP BY tool_name` 的调用次数。
- **效率指标**: `AVG(execution_time_ms)`，区分不同 `event_type`。
- **稳定性指标**: 捕获 `error_message` 频率最高的前 3 类错误。

## 2. API 响应结构示例

`GET /api/portal/dashboard/agent-stats`

```json
{
  "code": 200,
  "data": {
    "tool_usage": [
      { "name": "get_dataset_schema", "value": 150 },
      { "name": "execute_sql_query", "value": 85 }
    ],
    "health_stats": {
      "overall_success_rate": 94.5,
      "total_tool_calls": 235,
      "avg_cot_steps": 3.2
    },
    "performance_trend": [
      { "time": "10:00", "avg_ms": 1200 },
      { "time": "11:00", "avg_ms": 1550 }
    ],
    "recent_errors": [
      { "trace_id": "...", "tool": "execute_sql", "error": "Table not found" }
    ]
  }
}
```

## 3. 前端交互设计

- **可视化组件**: 复用 `Overview.vue` 中已集成的 ECharts 库。
- **异常穿透**: 异常列表中的 Trace ID 支持点击，点击后通过路由传参跳转到 `AgentDebug.vue`，自动弹出该 Trace 的日志详情抽屉。
- **权限**: 考虑到性能和隐私，该详细统计模块仅限管理员角色查看。
