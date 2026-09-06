## 1. 数据库与基础架构 (Database & Infrastructure)

- [x] 1.1 创建 `agent_scheduled_tasks` 数据库迁移脚本
- [x] 1.2 安装 `apscheduler` 及其数据库/Redis 依赖扩展
- [x] 1.3 在 `app/models/` 下定义任务相关的 SQLAlchemy 模型
- [x] 1.4 在 `app/core/` 下配置 APScheduler 初始化逻辑，接入 MySQL JobStore

## 2. 调度服务端实现 (Scheduler Service Implementation)

- [x] 2.1 实现 `TaskService` 类，封装任务的增删改查逻辑
- [x] 2.2 实现 `TaskRunner` 类，负责身份模拟（User Impersonation）逻辑
- [x] 2.3 实现 `TemplateProcessor`，支持 `{{yesterday}}` 等动态变量替换
- [x] 2.4 实现基于 Redis 的分布式锁机制，防止任务并发竞争执行
- [x] 2.5 集成 Webhook 推送通知功能，将 AI 执行结果发送到外部渠道

## 3. 任务中心 API 开发 (Task Center API)

- [x] 3.1 创建 `app/api/v1/endpoints/tasks.py` 接口文件
- [x] 3.2 实现 `GET /api/v1/tasks` (列表查询) 与 `GET /api/v1/tasks/{id}` (详情)
- [x] 3.3 实现 `POST /api/v1/tasks` (创建) 与 `PATCH /api/v1/tasks/{id}` (更新)
- [x] 3.4 实现 `POST /api/v1/tasks/{id}/run` (立即触发执行)
- [x] 3.5 实现 `GET /api/v1/tasks/{id}/logs` (执行历史查询)

## 4. 智能体系统工具集成 (Agent System Tools)

- [x] 4.1 在 `app/services/ai/tools/` 下新建 `task_manager_tools.py`
- [x] 4.2 实现 `create_recurring_task` 工具，支持自然语言解析 Cron
- [x] 4.3 实现 `get_my_tasks` 和 `modify_task` 工具
- [x] 4.4 在 `ToolRegistry` 中注册这些系统内置工具，并赋予核心智能体访问权限

## 5. 前端管理界面 (Frontend Task Center)

- [x] 5.1 创建“任务中心” Vue 页面及侧边栏菜单入口
- [x] 5.2 实现任务列表展示卡片，包含下一次运行时间、状态切换开关
- [x] 5.3 实现创建/编辑任务的弹窗表单，支持 Cron 表达式验证
- [x] 5.4 实现执行历史记录的抽屉详情页，展示 AI 回复内容

## 6. 测试与验收 (Testing & Verification)

- [x] 6.1 编写单元测试：验证 Cron 解析与变量替换逻辑
- [x] 6.2 编写集成测试：模拟多节点并发竞争 Redis 锁
- [x] 6.3 端到端测试：通过对话让 AI 创建定时任务并验证其准时执行