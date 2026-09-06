# 部署与验收手册

本手册用于在 Windows PowerShell 本地启动听书问数系统。不要提交 `.env`、`conf/app_config.local.yaml`、模型文件或 Docker 数据卷。

## 1. 初始化环境

复制环境变量样例并填写真实值：

```powershell
Copy-Item .env.example .env
uv sync --all-groups
docker compose --env-file .env -f infra/compose.yaml up -d
```

关键约束：`META_DB_PASSWORD` 必须与 `MYSQL_APP_PASSWORD` 一致；`AUDIO_DB_USER` 用于初始化数据；运行应用时使用 `AUDIO_QUERY_USER`，该用户只应拥有 `audio.*` 的 `SELECT` 权限。

首次创建 audio 库后，以管理员身份创建只读用户：

```sql
CREATE USER IF NOT EXISTS 'listenbook_reader'@'%' IDENTIFIED BY '<AUDIO_QUERY_PASSWORD>';
GRANT SELECT ON audio.* TO 'listenbook_reader'@'%';
FLUSH PRIVILEGES;
```

使用你的实际用户名和密码替换示例值。生产环境应改为受限来源网段，而不是 `%`。

### 安全配置（生产必读）

应用启动时会对 JWT 签名密钥做强度校验，规则与分级响应如下：

| 检查项 | 判定 | 开发环境（`APP_ENV=development`） | 其他环境（如 `production` / `staging`） |
|---|---|---|---|
| 命中硬编码默认值 `dev-only-secret-change-me` | 弱密钥 | WARNING 后放行 | **拒绝启动**（`RuntimeError`） |
| UTF-8 字节数 < 32（违反 RFC 7518 §3.2 HS256 推荐） | 弱密钥 | WARNING 后放行 | **拒绝启动**（`RuntimeError`） |

⚠️ 生产部署 `.env` 必须同时满足：

```bash
# 1. 设置环境为生产
APP_ENV=production

# 2. 生成 ≥ 32 字节的强密钥（推荐 48+）
# Bash:      openssl rand -base64 48
# PowerShell:[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }))
AUTH_SECRET_KEY=<上面生成的 base64 串>
```

校验在 `lifespan` 最早期、任何外部连接之前执行（fail-fast），避免半初始化状态。完整逻辑见 `app/core/security.py:validate_secret_key`，测试见 `tests/test_secret_key_validation.py`。

## 2. 创建数据与知识库

默认 smoke 数据适合日常开发；已有库不会被覆盖。只有明确传入 `--reset` 才会删除目标库。

```powershell
uv run --group data python -m tools.audio_data.bootstrap --profile smoke
uv run python -m app.scripts.build_meta_knowledge --domain audio
```

完整验收数据会更大，建议使用独立库：

```powershell
uv run --group data python -m tools.audio_data.bootstrap --profile full --schema audio_full --reset
```

重新构建知识库会创建新的版本并切换别名，不需要手动删除 Qdrant 或 Elasticsearch 索引。

## 3. 运行与检查

本地开发：

```powershell
uv run uvicorn main:app --reload
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

使用应用容器（推荐作为常驻服务）：

```powershell
cd infra
docker compose --env-file .env -f compose.app.yaml up -d --build
docker compose --env-file .env -f compose.app.yaml ps
```

`/health` 仅表示进程存活；`/ready` 会检查 MySQL、Qdrant、Elasticsearch 和 Embedding。浏览器访问 `http://127.0.0.1:8000/debug` 可查看 SSE 执行阶段、已校验 SQL、结果和解释。

### 前端工作台

正式问数工作台由 `web` 服务（Nginx + React SPA）提供，与 `app` 同在 `compose.app.yaml` 中：

```powershell
cd infra
docker compose --env-file .env -f compose.app.yaml up -d --build
# 工作台入口：http://127.0.0.1:8080/（可用 WEB_PORT 修改对外端口）
```

Nginx 同域托管前端并把 `/api/`、`/health`、`/ready`、`/debug` 反向代理到 FastAPI，`/api/` 已关闭代理缓冲并设置 300 秒读取超时以支持 SSE。生产环境只暴露 Nginx 端口，FastAPI 的 8000 端口保留在 Docker 内部网络；本地调试仍可直接 `uvicorn` 访问 8000。

前端本地开发与验收（Node 20+，详见 `frontend/README.md`）：

```powershell
cd frontend
npm ci
npm run typecheck
npm run test
npm run build
npx playwright install chromium   # 首次
npm run e2e
```

同步调用示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/query/sync `
  -Method Post -ContentType 'application/json' `
  -Body '{"query":"最近7天播放量最高的前10个专辑"}'
```

## 4. 验收命令

```powershell
uv lock --check
uv run python -m compileall -q app tests
uv run pytest -q
$env:RUN_AUDIO_DATA_ACCEPTANCE='1'
uv run pytest -q -m integration
```

默认 pytest 会运行 Fake LLM、SQL/PII 安全、UI 协议、指标口径和单元测试。真实数据/真实 LLM 验收须显式启用 `RUN_AUDIO_DATA_ACCEPTANCE`。

## 5. 备份与恢复

### MySQL

```powershell
# 备份业务库和元数据库（替换真实密码）
docker exec listenbook-data-agent-mysql mysqldump -uroot -p<MYSQL_ROOT_PASSWORD> --databases audio_full listenbook_meta > listenbook_$(Get-Date -Format yyyyMMdd).sql

# 恢复
docker exec -i listenbook-data-agent-mysql mysql -uroot -p<MYSQL_ROOT_PASSWORD> < listenbook_20260101.sql
```

### Qdrant

Qdrant 数据保存在命名卷 `docker_windows_qdrant_data`（或 `listenbook-data-agent_qdrant_data`，取决于 compose 项目名）。恢复知识库最直接的方式是重新执行：

```powershell
uv run python -m app.scripts.build_meta_knowledge --domain audio --force
```

### Elasticsearch

ES 数据保存在命名卷 `docker_windows_es_data`。重建索引同样通过上面的知识库构建命令完成。

### 日志

应用日志写入容器内 `/app/logs/app.log`，并通过 compose 挂载到项目 `logs/` 目录：

```powershell
Get-Content logs/app.log -Tail 50
```

## 6. 故障排查

| 现象 | 排查方式 |
| --- | --- |
| MySQL 1045 Access denied | Docker 数据卷保留的是首次启动时的密码。先核对 `.env` 与现有容器环境；只有确认不需要旧数据后才执行 `docker compose -f infra/compose.yaml down -v` 后重建。 |
| `/ready` 返回 503 | 逐项检查 `docker compose -f infra/compose.yaml ps`，再确认端口 `23306`、`16433`、`19200`、`8081` 没有被占用。 |
| 应用容器 healthcheck 失败 | 检查 `docker compose -f infra/compose.app.yaml logs app`，确认所有依赖 `/ready` 返回 200。 |
| 知识库构建失败 | 先确认 audio 数据库存在、Embedding 服务可用，再重新执行构建命令。失败版本不会切换活动别名。 |
| SQL 被拒绝 | 仅支持单条授权 `SELECT`。检查是否使用了敏感列、系统库、`SELECT *`、子查询、锁定查询或 OFFSET。 |

查询追踪保存在元数据库的 `query_trace` 与 `query_trace_phase` 表中，只保存请求、SQL、阶段耗时和错误，不保存结果行。
