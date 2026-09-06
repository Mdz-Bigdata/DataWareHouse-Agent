## 1. 数据库结构迁移 (Database Migration)

- [x] 1.1 编写 `db-prod` 分区升级 SQL DDL 脚本：变更 `ai_agent_access_logs` 和 `ai_agent_execution_traces` 为 `(id, created_at)` 联合主键，并预挂载当前及后续几个月份的按月 Range 分区。

## 2. 后端核心服务与 Scheduler 定时任务 (Backend Services & Scheduler Tasks)

- [x] 2.1 在 `ConfigService` 维护的系统参数中，添加日志配置参数 `audit_log_retention_days`（默认 90 天），并支持 Redis 缓存管理。
- [x] 2.2 实现数据库分区工具逻辑 `PartitionService`：
  - 查询数据库 `information_schema.partitions` 获取两表的分区元数据列表。
  - 实现分区扩容逻辑：自动识别并预先 `ALTER TABLE ADD PARTITION` 创建未来 2 个月的新分区。
  - 实现过期分区 Drop 逻辑：检测全部记录都已过期的自然月分区，执行物理 Drop 回收；对未分区环境，平滑退化为微批量 `DELETE`。
- [x] 2.3 注册自动化定时任务：在后台 `Scheduler` 模块中配置每日凌晨运行该分区维护任务。

## 3. 后端 API 路由与安全权限拦截 (API Routes & Security)

- [x] 3.1 新增日志管理相关 API 接口：
  - `GET /api/portal/system/logs/config` (获取日志保留天数配置)
  - `POST /api/portal/system/logs/config` (更新保存保留天数配置)
  - `GET /api/portal/system/logs/partitions` (读取日志分区物理信息列表)
  - `POST /api/portal/system/logs/cleanup` (手动触发清理日志)
- [x] 3.2 增加接口访问权限拦截，使用已有的权限和角色机制确保上述全部 4 个端点**仅限 `admin` 角色用户**有权调用。

## 4. 前端界面 Tab 与可视化控制 (Frontend UI)

- [x] 4.1 在 `SystemConfig.vue` 顶部 Tab 栏新增“日志管理” Tab 按钮，并通过 `hasPermission` 或 `userInfo.role === 'admin'` 限制可见性。
- [x] 4.2 实现“日志管理” Tab 控制台内容区：
  - 添加“日志保留期限（天）”输入组件与【保存配置】操作按钮。
  - 添加【立即手动清理】按钮，整合 `ConfirmModal` 确认组件提供二次确认保护。
  - 开发分区状态表格，展示分区表名、分区标识、数据边界和数据量行数。

## 5. 测试与 Checklist 验证 (Testing & Verification)

- [x] 5.1 在 `tests/` 目录下编写对应的接口与服务单元测试，涵盖配置读写校验、Admin 权限拦截、分区 Drop 与 Cleanup 的后端模拟逻辑。
- [x] 5.2 更新并完善 `tests/CHECKLIST.md` 自动化测试用例清单，在其中增列日志管理与表分区机制的测试项。
