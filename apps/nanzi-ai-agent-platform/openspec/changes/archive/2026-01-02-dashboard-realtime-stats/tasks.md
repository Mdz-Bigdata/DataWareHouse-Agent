# Tasks: Dashboard 实战化

## 后端开发 (Backend)
- [x] **设计统计接口**: 在 `app/api/portal/endpoints/dashboard.py` 中新增 `GET /agent-stats` 接口。 <!-- id: be-stats-api -->
- [x] **实现指标聚合**: 编写 SQL 查询 `ai_agent_execution_traces`，计算工具成功率、耗时百分位数等。 <!-- id: be-stats-logic -->
- [x] **扩展活跃数据**: 在 `recent-activities` 中加入“最近失败的工具调用”列表。 <!-- id: be-stats-recent -->

## 前端开发 (Frontend)
- [x] **新增统计卡片**: 在 `Overview.vue` 顶部增加“智能体健康度”系列卡片（如：工具成功率、平均 CoT 步数）。 <!-- id: fe-stats-cards -->
- [x] **集成工具分布图**: 使用饼图展示不同工具（get_schema, execute_sql 等）的调用频次。 <!-- id: fe-stats-pie -->
- [x] **集成耗时趋势图**: 使用折线图展示智能体思考与执行的平均耗时变化。 <!-- id: fe-stats-line -->
- [x] **异常链路列表**: 在仪表板下方增加一个简单的表格，展示最近 5 条状态为 'error' 的 Trace 记录，点击可跳转调试页查看详情。 <!-- id: fe-error-list -->
