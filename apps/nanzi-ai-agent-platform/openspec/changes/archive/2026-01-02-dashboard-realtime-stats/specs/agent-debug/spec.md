# agent-debug Specification Delta (Dashboard Stats)

## ADDED Requirements

### Requirement: Global Execution Visibility
系统 **MUST** 在仪表板中提供智能体运行状况的宏观统计。
- **Metrics**: 包含工具调用成功率、平均耗时、最活跃工具。
- **Periodicity**: 支持按“今日”、“本周”、“本月”进行筛选。

#### Scenario: View Tool Success Rate
- Given Admin is on the Overview page
- When They select "今日" as the time range
- Then A pie chart shows the distribution of tool call results (Success vs Error)
- And A card displays the overall success percentage

### Requirement: Performance Monitoring
系统 **MUST** 追踪智能体的响应性能。
- **Metric**: 记录每一步（Step）的平均执行时间。

#### Scenario: Identify Slow Tools
- Given Admin views the Performance Line Chart
- When They hover over a specific tool's line
- Then They can see the average latency trend for that tool over the last 24 hours
