## Context

当前智能体平台的 ChatBI 物理 SQL 查询完全代理给外部的 `yovole-yunshu-api-data-platform` HTTP 接口。这增加了运维管理成本，并且产生了跨服务的网络请求与鉴权耗时。本项目已具备 `meta_db_connection_configs` 数据源连接表和 `aiomysql`、`asynch`、`oracledb` 依赖，因此可以在平台内部自研集成直连物理执行层，并使用配置开关决定执行模式。

## Goals / Non-Goals

**Goals:**
- 在智能体平台内部构建一套数据库适配器模块（MySQL/ClickHouse/Oracle Adapter），支持 SELECT-only SQL 校验、参数安全绑定、执行及结果转换。
- 在平台内部实现连接池管理器 `DataSourcePoolManager`，并使其对接本地 `DbConnectionService`。
- 实现环境变量 `SQL_EXECUTION_MODE` 和数据库 `system_configs`（`sql_execution_mode`）双层分流控制，默认远程；环境变量为空或 `auto` 时支持通过后台配置无感动态切成本地。
- 支持在 `local` 模式下直接通过本地 Adapter 执行在线数据查询与 Debug 测试。
- 保持 `call_external_sql_api` 对上层智能体工具与 ChatBI 的返回格式兼容。

**Non-Goals:**
- 本次改动不涉及元数据表 Schema 的重新设计（已添加的 description 除外）。
- 不涉及向非 SELECT 查询之外的 DDL/DML/SHOW/DESCRIBE/EXPLAIN 操作提供支持。
- 不会弃用 remote 模式，以便用户在本地连接出错时可切换备份。

## Decisions

### 决策一：采用与外部数据平台相同的 Adapter 适配器与 PoolManager 结构
- **方案描述**：直接将外部执行系统的 `data_adapter`（ClickHouse, MySQL, Oracle）代码迁移整合至智能体平台中，复用其安全性校验和语法转义逻辑，同时引入 `DataSourcePoolManager` 做本地连接池维护。
- **选择理由**：外部系统的 Adapter 已经过生产验证，且由于第三方依赖库完全一致（`aiomysql`, `asynch`, `oracledb`），直接平移能保证 100% 逻辑兼容，降低实现风险。

### 决策二：配置开关采取“环境强制、数据库动态”的策略
- **方案描述**：在 `data_api.py` 的 SQL 执行入口中，首先读取环境变量 `SQL_EXECUTION_MODE`。当环境变量为 `local` 或 `remote` 时强制锁定对应模式；当环境变量为空或 `auto` 时读取数据库 `system_configs` 中 key 为 `'sql_execution_mode'` 的动态配置项；当数据库配置为空或非法时回退为 `remote`。
- **选择理由**：本地开发与容器部署可以通过环境变量强制指定，生产环境则通过不设置环境变量或设置为 `auto`，保留后台免重启动态切换能力。

### 决策三：连接池获取使用 SQLAlchemy AsyncSession 进行桥接
- **方案描述**：外部系统原先使用 `get_db_connection()` 自建 cursor，这里我们在 `pool_manager.py` 获取数据源配置时，通过 `app.core.orm.AsyncSessionLocal` 创建异步 DB 事务 Session，并调用 `DbConnectionService.get_config_by_name` 拉取配置并实例化连接池。数据源名称必须兼容历史外部执行标识，推荐命名为 `default_clickhouse`、`clickhouse_xxx`、`mysql_xxx`、`oracle_xxx`，本地执行按 `data_source` 字符串精确匹配 `meta_db_connection_configs.name`。
- **选择理由**：智能体平台数据库本身就是 SQLAlchemy ORM 映射的，利用本地已有的 ORM Service 最安全且对数据库事务无害。

### 决策四：本地执行保持 SELECT-only 且必须限流
- **方案描述**：本地模式只允许单条 `SELECT` 查询。所有本地执行在真正下发数据库前必须做 SQL 安全校验、多语句拦截、最大行数限制、单次查询超时限制和最大返回体限制；默认最大行数沿用外部数据平台的 1000 行。
- **选择理由**：ChatBI 查询由大模型生成，必须避免非预期库表探测、DDL/DML 修改、大结果集和长查询拖垮业务库。

### 决策五：`checkauth` 只做权限与重写校验，不执行数据库
- **方案描述**：`/api/v1/chatbi/sql/checkauth` 在 local/remote 模式下都不得执行数据库查询。它只负责数据集、数据表权限校验、行级权限重写和 SELECT 安全校验，并可返回校验结果或重写后的 SQL 调试信息。
- **选择理由**：权限检查接口应保持低成本和无副作用，避免用户仅测试权限时触发真实数据库访问。

## Risks / Trade-offs

- **[Risk] Oracle 数据库连接依赖 Oracle Client 物理库** ➡️ **[Mitigation]** 默认开启 Oracle Thin 模式，如果是需要 Thick 模式的用户，可以通过配置环境变量 `USE_ORACLE_THICK_MODE=1` 来显式开启，并通过 `lib_dir` 指定 client 路径。
- **[Risk] 本地连接池常驻造成数据库连接占满** ➡️ **[Mitigation]** 对连接池的最大大小做合理配置（MySQL/ClickHouse 最大限制为 100，Oracle 为 50），且提供健康的 ping 检测、空闲回收、服务关闭释放和数据源配置变更后的旧连接池失效关闭。
- **[Risk] 多进程部署导致连接数被 worker 数放大** ➡️ **[Mitigation]** 连接池大小按单进程配置，并在部署文档中说明总连接数约等于 `pool_size * worker_count * active_data_source_count`，生产配置需按数据库承载能力下调。
- **[Risk] 本地执行结果格式与远程接口不一致** ➡️ **[Mitigation]** 本地分支必须在 `call_external_sql_api` 内统一归一化为当前工具链已消费的 JSON 字符串或 `[TOOL_ERROR]...` 错误字符串。
