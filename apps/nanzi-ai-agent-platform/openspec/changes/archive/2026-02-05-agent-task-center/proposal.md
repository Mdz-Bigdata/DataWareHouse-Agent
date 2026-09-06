## Why

当前平台智能体仅支持“被动响应”模式，必须依赖用户输入才能执行。为了实现自动化运维、定期报表生成及主动监控，系统需要具备“主动执行”的能力。通过引入定时任务中心，智能体可以周期性地自主完成复杂业务逻辑，将平台从单一的聊天机器人升级为真正的自动化智能助手。

## What Changes

- **新增任务调度引擎**: 集成 APScheduler，利用 Redis 实现分布式锁，确保多实例环境下任务的唯一触发。
- **新增数据库架构**: 在 MySQL 中建立 `agent_scheduled_tasks` 表存储任务定义，并扩展执行历史记录以支持定时触发类型。
- **管理 API 与 UI**: 提供完整的 CRUD 接口及前端管理界面，支持手动启停、日志回溯及即时触发。
- **智能体执行上下文恢复**: 实现执行时的身份模拟机制，确保定时任务能以创建者的权限安全地访问数据。
- **变量预处理系统**: 支持 Prompt 中的动态时间变量（如 `{{yesterday}}`）自动替换。
- **多渠道结果推送**: 任务执行结果支持推送到 Webhook（钉钉、企业微信等）及内部审计日志。

## Capabilities

### New Capabilities
- `agent-task-scheduling`: 负责 Cron 解析、分布式触发及模拟用户请求的执行引擎。
- `task-management-api`: 提供任务定义、状态切换、日志查询及手动干预的标准 RESTful 接口。
- `agent-task-tools`: 为智能体提供的系统内置工具，使其能够查询、创建或修改定时任务。

### Modified Capabilities
- `agent-runtime`: 修改智能体运行环境，支持在无 Request 对象的情况下恢复 User 身份及注入外部 Context。

## Impact

- **后端**: `app/services/ai/` 目录下新增 `scheduler` 模块；修改 `agent_service.py` 以支持非交互式调用。
- **数据库**: 新增 `agent_scheduled_tasks` 表。
- **前端**: 新增“任务中心”管理菜单及对应的 Vue 组件。
- **依赖**: 引入 `APScheduler` 作为调度库，深度依赖 Redis 锁机制。
