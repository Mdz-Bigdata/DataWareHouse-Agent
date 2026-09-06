## ADDED Requirements

### Requirement: 日志保留期限配置 (Log Retention Configuration)
系统必须允许管理员（Admin）在系统配置界面中设置并保存日志保留天数（参数为 `audit_log_retention_days`），其数值必须为正整数（1 到 3650 天）。

#### Scenario: 成功更新并应用保留天数配置
- **WHEN** 管理员用户提交合法的保留天数（例如 90）并保存
- **THEN** 系统成功写入 `system_configs` 数据库表，刷新 Redis 与内存缓存，并提示保存成功。

#### Scenario: 提交非法保留天数校验失败
- **WHEN** 管理员用户提交非正整数（例如 -5 或 abc）的保留天数
- **THEN** 系统接口返回 400 校验错误，且数据库配置不发生变更。

### Requirement: 分区表状态监视 (Partition Monitoring)
系统必须能够读取并向管理员展示 `ai_agent_access_logs` 与 `ai_agent_execution_traces` 两张核心日志表的物理 Range 分区状态（分区名、截至边界范围和估算记录行数）。该数据应当仅对 `admin` 角色开放。

#### Scenario: Admin 用户成功查询分区状态
- **WHEN** 拥有 `admin` 角色的用户请求分区列表 API 接口
- **THEN** 系统从 `information_schema.partitions` 检索包含分区名称、边界描述以及 `TABLE_ROWS` 行数的列表，并返回 200 响应。

#### Scenario: 非 Admin 用户请求分区状态被阻断
- **WHEN** 普通用户请求分区列表 API 接口
- **THEN** 系统接口拦截并返回 403 权限不足响应，且不透露任何分区元数据。

### Requirement: 手动清理历史日志 (Manual Log Cleanup)
系统必须允许管理员手动触发清理过期日志的操作。当执行清理时，必须优先以 Drop 过期整月分区的方式执行以保障数据库无锁秒级清理；若系统处于非分区单表状态，则必须使用微批量 DELETE 进行平滑清理以防止锁表。

#### Scenario: 手动清理成功触发并回收分区
- **WHEN** 管理员在配置页中点击“立即手动清理”
- **THEN** 后端读取配置的保留期限，自动定位满足全部数据过期的整月分区并执行 `ALTER TABLE DROP PARTITION` 回收物理空间，返回已清理的分区列表或已删除的数据行数。

### Requirement: 定时自动分区维护与回收 (Automated Partition Maintenance)
系统必须通过 Scheduler 定时任务在每日凌晨自动运行分区维护，保证新数据能够正常落入预建的分区中，且过期数据能自动被 Drop 物理丢弃。

#### Scenario: 定时任务自动扩容与过期回收
- **WHEN** 每日凌晨定时调度触发分区维护任务
- **THEN** 系统自动为未来 2 个月预创建按月分区的 Range 节点，并检查保留天数配置，自动 Drop 掉符合过期时限的整月历史分区。
