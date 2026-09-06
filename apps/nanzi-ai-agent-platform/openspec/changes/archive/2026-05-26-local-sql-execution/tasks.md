## 1. 适配器与连接池模块搭建

- [x] 1.1 创建本地适配器目录 `app/services/data_adapter/` 并移植 `base.py` 核心拦截逻辑
- [x] 1.2 移植并优化 `mysql.py`、`clickhouse.py`、`oracle.py` 物理数据源执行引擎
- [x] 1.3 移植 `models.py` 与 `factory.py` 适配器构造逻辑，并使其兼容本地 ORM 数据源别名查询
- [x] 1.4 创建并实现本地连接池管理服务 `app/services/pool_manager.py`，使之能基于本地 `DbConnectionService` 拉取连接信息并维护池生命周期
- [x] 1.5 支持数据源配置修改/删除后的连接池失效关闭，以及应用 shutdown 时关闭全部本地连接池
- [x] 1.6 为 MySQL、ClickHouse、Oracle Adapter 和 PoolManager 增加单元测试，覆盖连接参数、异常归一化和连接池失效

## 2. 工具分流与配置开关实现

- [x] 2.1 在数据库的系统配置 `system_configs` 表中新增 `'sql_execution_mode'` 配置项，并在前端配置页面中进行展现
- [x] 2.2 改造 `app/services/ai/tools/data_api.py` 的 `call_external_sql_api` 方法，按 `SQL_EXECUTION_MODE=local|remote` 强制优先、`SQL_EXECUTION_MODE` 为空或 `auto` 时读取数据库 `sql_execution_mode` 的规则进行本地/远程执行分流
- [x] 2.3 本地分支成功时归一化为现有 JSON 字符串返回，失败时归一化为 `[TOOL_ERROR]...`，保持智能体工具和 ChatBI 调用链兼容
- [x] 2.4 增加分流测试，覆盖环境变量强制、数据库动态配置、非法配置回退 remote，以及 local 模式不发起 HTTP 请求

## 3. 在线数据查询 Debug 功能集成

- [x] 3.1 改造在线只读 SQL 执行接口 `/api/v1/chatbi/sql/execute`，使其在本地模式下直接通过直连 Adapter 执行 SQL 语句
- [x] 3.2 改造校验接口 `/api/v1/chatbi/sql/checkauth`，支持在本地直连模式下完成数据集/数据表权限校验、行级权限重写和 SELECT 安全校验，但不得连接或执行数据库
- [x] 3.3 本地执行必须只允许 SELECT 查询，拒绝 UPDATE/INSERT/DELETE/DROP/SHOW/DESCRIBE/EXPLAIN 和多语句
- [x] 3.4 本地执行必须实现最大 1000 行默认限制、超时限制、最大返回体限制，并按数据库方言处理显式 LIMIT 超上限的场景
- [x] 3.5 增加 API 测试，覆盖 execute 成功、SQL 安全拦截、结果限制、超时错误、checkauth 不执行数据库、数据源名称不存在错误

## 4. 数据源管理兼容提示

- [x] 4.1 在数据源管理新增/编辑表单中提示用户按 `default_clickhouse`、`clickhouse_xxx`、`mysql_xxx`、`oracle_xxx` 格式维护数据源名称
- [x] 4.2 自动生成数据源名称时使用下划线兼容格式，确保后续本地执行可按 `data_source` 字符串精确匹配
