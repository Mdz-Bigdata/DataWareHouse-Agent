# NanZi 完整平台集成

两个完整上游项目已经固定版本导入 `apps/nanzi-api-data-platform` 与
`apps/nanzi-ai-agent-platform`。本目录提供初始化和运行适配，不修改上游源码快照。
能力中心的“外部面板”打开完整 Vue 管理界面，由各自 FastAPI 后端托管。

## 启动

在仓库根目录运行：

```bash
./start.sh
```

`start.sh` 自动启动已安装的 Docker Desktop/OrbStack、本地核心门户和 API，
构建两个 NanZi 完整镜像，启动 MySQL、各自 Redis 与统一网关，
等待数据库健康、执行一次性初始化，然后启动应用并检查所有入口。
首次镜像构建包含两个 Vue
前端、数据库驱动和智能体浏览器依赖，耗时取决于网络和机器资源。
重复启动复用构建缓存与数据库卷。无需分别执行平台启动命令。

主门户默认连接独立持久化 PostgreSQL 数仓，首次迁移项目示例数据，并明确显示
数据库类型和初始数据来源；再次启动保留已有记录，原有 `.env` 不会被改写。
使用真实业务库时可设置 `CORE_DB_TYPE` 与可选 `CORE_DB_URL`，详见
[本地启动说明](../../docs/local-start.md)。完整容器部署仍可使用 `./platform.sh up-nanzi`。

| 入口 | 本机默认地址 | 内容 |
| --- | --- | --- |
| 统一门户 | http://localhost:5173 | 能力中心与原有数仓功能 |
| NanZi 数据服务平台 | http://localhost:8020 | 完整登录页、SQL Lab、数据源、目录、API 与权限管理 |
| NanZi 智能体平台 | http://localhost:8030 | 完整登录页、ChatBI、智能体、工具、知识库与任务管理 |
| 能力网关 | http://localhost:8080/api/platform/capabilities | 平台注册信息 |

两个平台均使用用户名 `admin`。登录页选择“本地账号”可使用已设置的密码；
密码哈希保存在各自数据库中，重复启动不会重置。首次初始化尚未设置密码时，
先使用“API Key”登录并设置本地账号密码。登录 API Key 分别在本机私有 `.env.platform` 的
`DATA_API_ADMIN_API_KEY`、`AGENTS_ADMIN_API_KEY` 中。初始化不会在日志打印密钥。
该文件权限为 `0600` 且已被 Git 忽略；再次 `init` 保留文件，不重新生成凭据。
请将它与数据库卷一起备份，不要在已初始化的数据库上更换加密密钥。

## 平台连接与业务配置

初始化器将智能体的 SQL 服务指向
`http://data-api:8000/api/v1/sql/execute`，配置本项目的数据平台 API Key。
两个应用拥有独立管理库与会话，未假定它们共用登录账号或模型配置。

首次登录后，在数据服务平台登记真实业务数据源，在智能体系统配置中选择其
`external_sql_data_source`，并配置自己的模型服务。可在首次初始化前使用
`DATA_PLATFORM_DATA_SOURCE` 预设数据源键。上游示例数据源、模型密钥和远程工具
已禁用或清空；未配置真实模型与数据源时，不会获得真实 ChatBI 问数结果。

默认按钮使用访问门户时的主机名和 `8020/8030` 端口，避免远程访问时跳到访问者
自己的 localhost。通过 HTTPS 代理或独立域名部署时，在 `.env.platform` 设置
`PLATFORM_DATA_API_UI_URL`、`PLATFORM_AGENTS_UI_URL` 为完整公开地址，同时设置
`AGENTS_PUBLIC_URL`，用于智能体生成可访问的报告链接。

两个上游应用使用根路径资源、路由和 API。`/platform/data-api[/]` 与
`/platform/agents[/]` 因此重定向到原生完整应用；更深的网关路径继续提供 API 代理。
开发模式 `frontend` 的 Vite 同样代理能力 API 与启动路由到本地 `8080` 网关，
可用 `PLATFORM_GATEWAY_URL` 覆盖。

## 数据与会话隔离

- 初始化只接管空数据库；已有业务表但无初始化账本的数据库会被拒绝。
- 所有编号 SQL 按数字版本顺序执行，并记录文件摘要和执行状态。重复启动保留
  表、管理员、模型及数据源配置。执行中断或失败后不自动重放可能包含 `DROP TABLE`
  的迁移，需人工检查专用库或恢复备份；不可通过删账本跳过检查。
- 若已有此 Compose 项目的 MySQL 卷，使用原密码配置 `.env.platform`，不要为了
  启动而删除原卷。外部数据库覆盖方式见根目录 `.env.platform.example`。
- 适配器将两套 `admin_token` Cookie 分别映射为 `nanzi_data_admin_token` 和
  `nanzi_agents_admin_token`，保留 Cookie 标志、退出清理及流式响应。
- 两套 Redis 独立运行。智能体使用带搜索能力的
  [Redis Stack Server](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-stack/docker/)。
- 上传文件、品牌文件和智能体技能使用独立持久卷。`./platform.sh down` 不删除卷。

## 验证

```bash
./platform.sh verify
cd frontend
npm run build
node --test tests/platformLinks.test.mjs
```

本地验证包括源码快照校验、迁移账本与 SQL 解析、凭据生成、Cookie/SSE/WebSocket
适配、入口跳转与禁用状态，以及 Compose 配置校验。它们不替代真实 MySQL 迁移、
镜像构建或浏览器登录验收。启动后应检查两个平台的登录、退出、资源加载与业务配置；
容器未健康时查看 `docker compose --env-file .env.platform logs data-api-init agents-init`。
