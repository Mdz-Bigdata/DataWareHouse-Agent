# Change: Dashboard Real-time Stats

## Why
我们已经成功实现了智能体执行链路（Trace Logs）的采集与持久化。然而，这些数据目前主要用于单个会话的调试。为了从宏观角度监控智能体的运行状况（如工具调用成功率、平均思考时长、最常使用的工具等），我们需要将这些结构化的 Trace 数据集成到系统的仪表板（Dashboard）中。

## 目标 (Goal)
在现有的仪表板（Overview）中增加专门针对智能体运行状况的统计模块，通过图表直观展示智能体在执行过程中的关键指标，提升系统的可观测性和实战价值。

## What Changes
- **后端 (Backend)**:
  - 扩展 `dashboard` 端点，增加针对 `ai_agent_execution_traces` 表的统计聚合。
  - 提供指标包括：工具调用成功率、工具使用频率分布、智能体平均处理时长趋势等。
- **前端 (Frontend)**:
  - 在 `Overview.vue` 中新增“智能体运行统计”区块。
  - 使用 ECharts 展示工具调用占比图（饼图）和性能趋势图（折线图）。
  - 增加“异常执行链路”快速查看入口。

## 计划 (Timeline)
- **Phase 1**: 后端聚合指标 API 开发。
- **Phase 2**: 前端 ECharts 组件开发与集成。
- **Phase 3**: 整体联调与 UI 美化。
