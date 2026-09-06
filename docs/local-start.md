# 本地一键启动

在项目根目录运行 `./start.sh`，也可以从其他目录通过脚本的绝对路径运行。

启动器会自动启动当前 Docker 上下文对应的 Docker Desktop 或 OrbStack，准备私有配置，启动持久化 PostgreSQL 数仓、核心后端、前端、网关、两套完整 NanZi 应用及其 MySQL / Redis 依赖。首次运行会安装缺少的本地依赖、构建容器镜像并初始化空数据库；全部健康检查通过后才显示启动成功。首次使用需已安装 Python、Node.js 和 Docker Desktop 或 OrbStack，并允许首次镜像下载。

| 页面 | 本地地址 |
| --- | --- |
| 主平台 | http://localhost:5173 |
| NanZi 数据服务完整页面 | http://localhost:8020 |
| NanZi 智能体完整页面 | http://localhost:8030 |
| 核心 API 文档 | http://localhost:8000/docs |

主平台的“外部面板”分别进入数据服务和智能体完整应用。NanZi 管理员账号为 `admin`。登录页选择“本地账号”可使用已设置的管理员密码；密码以哈希形式保存在各自数据库中，停止或重新启动服务不会重置密码。初次初始化尚未设置密码时，可先选择“API Key”登录，再设置本地账号密码。两个应用的登录密钥分别是根目录 `.env.platform` 中的 `DATA_API_ADMIN_API_KEY` 和 `AGENTS_ADMIN_API_KEY`。该私有文件首次生成后保留，不会在启动日志中打印密钥。平台初始化和业务模型配置见 [NanZi 集成说明](../integrations/nanzi/README.md)。

核心问数默认连接本项目独立的 PostgreSQL 16 数仓。首次启动将原示例数仓的 8 张表及数据迁移进去；交易、听书和文章数据以真实 PostgreSQL 表保存，查询和归因都在该数据库执行。界面显示“PostgreSQL 数仓”，同时说明初始数据来自项目示例，避免与真实业务采集数据混淆。原有业务 `.env` 不会被改写。

PostgreSQL 默认连接地址为 `127.0.0.1:55432`，数据库名 `datawarehouse`，用户名 `warehouse`；密码位于私有 `.env.platform` 的 `WAREHOUSE_POSTGRES_PASSWORD`，该文件也支持配置端口、库名和用户名。数据库持久化在 Compose 的 `warehouse-postgres-data` 卷中。迁移器记录 `warehouse_meta.bootstrap` 归属和初始行数；再次启动只验证，不重置、重新生成或覆盖已存储的数据。没有归属记录的非空数据库会被拒绝导入。迁移后日期固定为导入时的数据日期，不会随重启滚动生成新业务记录。

需要使用原业务数据源时，在启动环境中明确设置 `CORE_DB_TYPE` 为其数据库类型，并按需设置 `CORE_DB_URL`。显式指定的数据库不会自动导入项目示例；查询失败也不会回退到 SQLite。仅需要内存测试时，可运行 `CORE_DB_TYPE=sqlite ./start.sh`。

`./start.sh --check` 只检查所有服务是否健康。`./start.sh --stop` 或启动终端的 `Ctrl+C` 会优雅停止本次启动的服务。已运行且属于本项目的健康前后端会被复用；启动器不会终止其他程序占用的端口。此前已运行的 Docker 服务和持久化数据库卷保留。

前后端日志位于 `.runtime/backend.log` 和 `.runtime/frontend.log`。NanZi 日志可通过 `docker compose --env-file .env.platform --profile nanzi logs data-api agents platform-gateway` 查看。启动失败会给出具体阶段；不会只启动主界面就提示全部成功。
