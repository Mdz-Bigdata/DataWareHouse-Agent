## ADDED Requirements

### Requirement: 本地与远程执行模式分流
系统必须支持通过环境变量 `SQL_EXECUTION_MODE` 和数据库 `system_configs` 动态配置项 `sql_execution_mode` 进行分流判定。当环境变量为 `local` 或 `remote` 时，系统必须强制使用环境变量指定的模式；当环境变量为空或 `auto` 时，系统必须读取数据库配置项 `sql_execution_mode`。当最终模式为 `local` 时，SQL 物理执行请求必须直接调用本地的适配器执行；当最终模式为 `remote`、未配置或非法值时，必须降级走外部 HTTP 接口请求。

#### Scenario: 本地模式分流执行
- **WHEN** `SQL_EXECUTION_MODE` 为空或 `auto`，且系统配置 `sql_execution_mode` 为 `local`，并调用 `call_external_sql_api` 工具接口
- **THEN** 系统直接调用本地 Adapter 驱动连接并执行数据库查询，不发起任何 HTTP 网络请求

#### Scenario: 环境变量强制远程模式
- **WHEN** `SQL_EXECUTION_MODE` 为 `remote`，且系统配置 `sql_execution_mode` 为 `local`
- **THEN** 系统必须使用远程 HTTP 执行模式，不得读取本地 Adapter 执行 SQL

#### Scenario: 远程模式降级执行
- **WHEN** 最终模式为 `remote`、未配置或非法值，且调用 `call_external_sql_api` 工具接口
- **THEN** 系统通过 HTTP 协议将请求发送至远程外部 SQL 执行 API（即 `external_sql_api_url`）

### Requirement: 数据源名称兼容历史执行标识
本地执行必须通过请求中的 `data_source` 字符串精确匹配 `meta_db_connection_configs.name`。数据源管理界面必须提示用户使用兼容历史外部执行服务的名称格式，例如 `default_clickhouse`、`clickhouse_xxx`、`mysql_xxx`、`oracle_xxx`。

#### Scenario: 按历史标识匹配本地数据源
- **WHEN** ChatBI 或智能体工具传入 `data_source` 为 `default_clickhouse`
- **THEN** 本地执行器必须查找名称为 `default_clickhouse` 的数据源配置，并使用该配置创建或复用连接池

#### Scenario: 本地数据源不存在
- **WHEN** 最终模式为 `local`，但请求中的 `data_source` 无法匹配任何数据源名称
- **THEN** 系统必须返回兼容现有工具链的 `[TOOL_ERROR]...` 错误，不得静默回退到任意默认数据源

### Requirement: 本地适配器直连与安全过滤
本地适配器必须支持 MySQL、ClickHouse、Oracle 等主流数据源的直接连接执行，且必须对执行的 SQL 进行安全性校验：仅允许单条以 `SELECT` 开头的查询，必须防止多语句执行，必须拒绝 `EXPLAIN`、`DESCRIBE`、`SHOW`、`UPDATE`、`INSERT`、`DELETE`、`DROP` 等非 SELECT 查询或探测语句。

#### Scenario: 执行合法只读 SQL
- **WHEN** 调用本地适配器执行合法的 SELECT 只读查询语句
- **THEN** 系统建立物理连接，执行 SQL 并成功返回格式为 `{"columns": [...], "items": [...]}` 的数据集

#### Scenario: 执行非法修改 SQL
- **WHEN** 调用本地适配器执行包含 UPDATE/INSERT/DELETE/DROP 等修改操作的 SQL
- **THEN** 系统安全校验拦截并抛出 `SQLSafetyError` 安全违规异常

#### Scenario: 执行结构探测 SQL
- **WHEN** 调用本地适配器执行 EXPLAIN/DESCRIBE/SHOW 等结构探测语句
- **THEN** 系统安全校验拦截并抛出 `SQLSafetyError` 安全违规异常

### Requirement: 本地执行结果限制与超时保护
本地执行必须在 SQL 下发数据库前应用最大行数限制，默认不超过 1000 行；必须应用单次查询超时限制；必须限制最大返回体大小；必须在超时、返回体过大或数据库错误时返回兼容现有工具链的错误格式。

#### Scenario: 查询未显式 LIMIT
- **WHEN** 本地模式执行的 SELECT 查询未包含显式 LIMIT
- **THEN** 系统必须按数据库方言自动追加或包裹最大 1000 行限制

#### Scenario: 查询显式 LIMIT 超过上限
- **WHEN** 本地模式执行的 SELECT 查询包含超过系统上限的 LIMIT
- **THEN** 系统必须将最终下发 SQL 的返回行数限制在系统上限内

#### Scenario: 查询超时
- **WHEN** 本地模式执行的 SELECT 查询超过配置的超时时间
- **THEN** 系统必须中止或释放该查询关联资源，并返回兼容现有工具链的 `[TOOL_ERROR]...` 错误

### Requirement: 本地执行返回格式兼容
`call_external_sql_api` 在 local 与 remote 模式下必须对上层保持兼容：成功时返回 JSON 字符串，内容包含 `columns` 与 `items`；失败时返回现有工具链可识别的 `[TOOL_ERROR]...` 字符串，不得直接向智能体工具泄漏 adapter 原始异常对象。

#### Scenario: 本地执行成功归一化
- **WHEN** 本地 Adapter 返回结构化查询结果
- **THEN** `call_external_sql_api` 必须将其归一化为 JSON 字符串并返回给调用方

#### Scenario: 本地执行失败归一化
- **WHEN** 本地 Adapter 抛出连接、权限、安全校验或超时异常
- **THEN** `call_external_sql_api` 必须返回以 `[TOOL_ERROR]` 开头的字符串，并保留可读错误原因

### Requirement: 数据源管理 SQL 在线 Debug 测试
系统在本地直连模式下，在线只读 SQL 执行接口 `/api/v1/chatbi/sql/execute` 必须能直接基于本地 Adapter 执行 SQL，完成 SELECT-only 校验、数据集/数据表权限校验、行级权限重写、结果限制并返回查询结果集。校验接口 `/api/v1/chatbi/sql/checkauth` 必须只执行权限校验、行级权限重写和 SELECT 安全校验，不得连接或执行数据库。

#### Scenario: 在线 SQL 查询 Debug 成功
- **WHEN** 管理员在数据源调试中输入只读 SQL 并点击测试查询
- **THEN** 系统通过本地对应数据源适配器执行查询，并在界面直观呈现字段列与数据行信息

#### Scenario: SQL 权限校验不执行数据库
- **WHEN** 调用 `/api/v1/chatbi/sql/checkauth` 检查 SQL 权限
- **THEN** 系统必须完成数据集/数据表权限校验、行级权限重写和 SELECT 安全校验，并且不得创建连接池或向目标数据库下发 SQL

### Requirement: 本地连接池生命周期管理
本地连接池管理器必须支持按数据源懒加载连接池、健康检查、空闲回收、服务关闭释放，以及数据源配置新增、修改、删除后的连接池失效关闭。

#### Scenario: 数据源配置修改后关闭旧连接池
- **WHEN** 已存在连接池的数据源被修改 host、port、user、password、database 或 db_type
- **THEN** 系统必须关闭该数据源旧连接池，并在下一次查询时按最新配置重新创建连接池

#### Scenario: 服务关闭释放连接池
- **WHEN** 应用进程进入 shutdown 生命周期
- **THEN** 系统必须关闭所有已创建的本地数据源连接池，避免连接泄漏
