## 1. 数据库与后端 (Database & Backend)
- [ ] 1.1 创建数据库迁移脚本，为 `ai_agents` 表添加 `is_enabled` 列。
- [ ] 1.2 更新后端 `AIAgent` 的 SQLAlchemy 模型和 Pydantic Schema 定义。
- [ ] 1.3 更新智能体 CRUD 接口，确保在创建/更新时处理 `is_enabled` 字段，并在列表查询时支持过滤。

## 2. 前端实现 (Frontend Implementation)
- [ ] 2.1 重构 `AgentManagement.vue` 页面：
    - 实现带筛选功能（状态、类型）的工具栏。
    - 重新设计智能体卡片（包含状态指示器、版本统计、最后更新时间）。
- [ ] 2.2 创建 `AgentVersionsDrawer.vue` (或 Modal) 组件，将版本管理逻辑（列表、创建、编辑、发布）封装其中。
- [ ] 2.3 实现智能体的“启用/禁用”切换操作，并对接后端 API。

## 3. 验证 (Verification)
- [ ] 3.1 验证数据库迁移是否成功执行。
- [ ] 3.2 验证前端列表的筛选功能和状态切换功能是否正常。
- [ ] 3.3 验证版本管理的抽屉/弹窗是否能正常打开并完成所有版本操作。
