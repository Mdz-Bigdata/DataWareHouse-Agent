# 项目收尾计划

> 本文档归档 ListenBook-DataAgent 的当前进度与剩余工作。
> 更新时间：2026-07-17
> 当前状态：主体功能已完成并容器化运行；剩余工作以“交付前必须完成”为最高优先级。

---

## 当前状态

- **Git**：`main` 与 `origin/main` 同步。
- **运行环境**：Windows 本机 Docker，应用容器 `listenbook-data-agent-app` 在 `0.0.0.0:8000` 健康运行。
- **数据**：业务库 `audio_full`（全量：2 万用户、30 万播放、6 万订单、12 万搜索日志）；元数据库 `listenbook_meta`；active 知识库 build `afccc364aa9c`。
- **依赖服务**：MySQL（23306）、Qdrant（16433）、Elasticsearch（19200）、Embedding TEI（8480）。
- **账号隔离**：
  - `listenbook_reader` / `<AUDIO_QUERY_PASSWORD>`：`audio_full.*` 只读；
  - `listenbook_app` / `<META_APP_PASSWORD>`：`listenbook_meta.*` 读写；
  - 应用不再使用 root。

---

## 1. Must-do（交付前必须完成）

### 1.1 Git 同步 ✅
已推送并验证 `git log origin/main..HEAD` 为空。

### 1.2 修复 `main.py` 并移除 demo 路由 ✅
- 修正 `from urllib.request import Request` 为 `from fastapi import Request`；
- 中间件 `print` 改为 `logger.debug`；
- 删除 `app/api/routers/test_router.py` 及其挂载。

### 1.3 补齐 PRD 真实模型闭环验收用例 ✅
`tests/test_prd_acceptance.py` 已覆盖 10 个场景，全部通过（运行约 12 分钟）。

### 1.4 为 45 个指标增加口径基准 SQL 校验 ✅
`tests/test_metric_baselines.py` 已验证 45 个指标语法、字段存在性、仓库执行。

### 1.5 跑通最终验收命令 ✅
```bash
uv lock --check
uv run python -m compileall -q app tests
uv run pytest -q          # 119 passed, 57 skipped
$env:RUN_AUDIO_DATA_ACCEPTANCE='1'; uv run pytest -q -m integration
```

- 前 3 条已通过。
- integration 测试共 57 个，分三类验证通过：
  - `tests/test_prd_acceptance.py`：10/10 passed
  - `tests/test_metric_baselines.py`：45/45 passed
  - `tests/test_audio_data_acceptance.py`：2/2 passed
- 注意：运行 integration 需要在项目根目录存在 `.env` 文件（已创建，gitignored），供 `tools.audio_data.bootstrap` 读取数据库密码。

---

## 2. Should-do（上线/维护前建议完成）

### 2.1 补齐单元测试、HTTP 接口测试和共享 fixtures
- **新增/修改文件**：
  - `tests/conftest.py`
  - `tests/test_agent_nodes.py`
  - `tests/test_meta_knowledge_service.py`
  - `tests/test_api.py`
  - `pytest.ini`
  - `pyproject.toml`
- **验证**：`uv run pytest -q` 全部通过。

### 2.2 清理代码异味和无用 demo 文件
- **删除文件**：
  - `app/clients/mysql_client_manager1.py`
  - `app/clients/mysql_client_manager2.py`
  - `app/clients/mysql_client_manager3.py`
  - `app/conf/test_config.py`
  - `conf/test_config.yaml`
  - `app/api/routers/test_router.py`
  - `app/agent/nodes/filter_table.py`
  - `app/agent/nodes/filter_metric.py`
  - `prompts/filter_table_info.prompt`
  - `prompts/filter_metric_info.prompt`
- **修改文件**：
  - `app/core/lifespan.py`、`app/agent/dependencies.py`：`print` → `logger`；
  - `app/agent/nodes/extract_keywords.py`：删除 stale TODO 注释。
- **验证**：
  ```bash
  grep -R "^ *print(" app --include="*.py"
  python -m compileall -q app tests
  uv run pytest -q
  ```

### 2.3 对齐配置与文档
- **修改文件**：
  - `AGENTS.md`：更新构建命令为 `conf/domains/audio/*.yaml`，补充 `tests/` 说明；
  - `README.md`：改为项目简介、快速启动、架构说明；
  - `pyproject.toml`：替换 `description` 占位符；
  - `conf/meta_config.yaml`：删除或移至 `docs/examples`；
  - `.env.example`：区分 `AUDIO_DB_USER`（初始化）与 `AUDIO_QUERY_USER`（应用只读）。
- **验证**：人工走读文档，命令可复制执行。

### 2.4 提升运行时可运维性
- **修改文件**：
  - `infra/Dockerfile`：非 root 用户运行；
  - `infra/compose.app.yaml`：healthcheck 从 `/health` 改为 `/ready`；
  - `infra/compose.yaml`：为 `embedding` 增加健康检查；
  - `app/clients/mysql_client_manager.py`：增加 `pool_pre_ping=True` 与超时参数；
  - `docs/DEPLOYMENT.md`：补充备份策略与日志查看说明。
- **验证**：
  ```bash
  docker compose -f infra/compose.yaml -f infra/compose.app.yaml config
  docker compose -f infra/compose.yaml -f infra/compose.app.yaml up -d
  curl -f http://127.0.0.1:8000/ready
  docker compose ps
  ```

---

## 3. Nice-to-have（有时间再做）

- **可观测性**：增加 `/metrics` 端点。
- **CI/CD**：Gitee/GitHub Action 跑 `uv lock --check`、`compileall`、`pytest`。
- **代码质量工具**：引入 `ruff` / `mypy`。
- **高级召回节点**：如 recall 不足，再把 `filter_table` / `filter_metric` 接入 graph。
- **安全测试扩充**：增加 `UNION`、`information_schema`、`LOAD_FILE`、`BENCHMARK` 等注入用例。
- **成本跟踪**：在 `query_trace` 中记录 LLM token 数/耗时。

---

## 关键文件清单

- `main.py`
- `app/agent/graph.py`
- `app/clients/mysql_client_manager.py`
- `app/services/meta_knowledge_service.py`
- `conf/domains/audio/metrics.yaml`
- `infra/Dockerfile`
- `infra/compose.app.yaml`
- `infra/compose.yaml`
- `docs/DEPLOYMENT.md`
- `AGENTS.md`
- `README.md`
- `pyproject.toml`

---

## 验证方式汇总

1. 本地命令验收：
   ```bash
   uv lock --check
   uv run python -m compileall -q app tests
   uv run pytest -q
   $env:RUN_AUDIO_DATA_ACCEPTANCE='1'; uv run pytest -q -m integration
   ```
2. 接口验证：
   ```bash
   curl http://127.0.0.1:8000/health
   curl http://127.0.0.1:8000/ready
   # 通过 /api/query/sync 跑 PRD 10 类问题
   ```
3. 容器验证：
   ```bash
   docker compose ps
   docker compose logs -f app
   ```

---

*说明：本计划基于“本机 Windows 作为测试+生产环境”的现状。若后续迁移到 Linux 云服务器，需额外处理网络、持久化卷、非 root 运行、HTTPS/域名等。*
