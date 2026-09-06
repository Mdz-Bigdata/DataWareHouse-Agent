# 听书问数系统

基于 FastAPI + LangGraph 的自然语言数据查询代理。用户用中文提问，系统自动理解意图、检索元数据、生成并校验只读 SQL、执行查询并返回解释。

## 项目地址

https://gitee.com/laixiaogang/listen-book-data-agent.git

## 功能特性

- **自然语言问数**：支持专辑/章节、播放、评论、收藏、会员、订单退款、搜索点击率、趋势、对比、Top 排行等场景。
- **流式与同步接口**：`POST /api/query` 返回 SSE 事件流；`POST /api/query/sync` 返回完整结构化结果。
- **SQL 安全**：基于 sqlglot 的 AST 白名单，仅允许单条 `SELECT`，禁止子查询、`SELECT *`、敏感列、锁定语句等。
- **元数据知识库**：54 张表、45 个指标、版本化构建，支持失败回滚。
- **数据生成**：`tools/audio_data/bootstrap` 支持 smoke / full 两种规模，显式 `--reset` 防止误删。

## 快速开始

本地启动、环境变量、知识库构建、健康检查、验收和故障排查请参见 [部署与验收手册](docs/DEPLOYMENT.md)。

> ⚠️ **生产部署前必读**：应用启动会对 JWT 签名密钥做强度校验，`APP_ENV=production` 且 `AUTH_SECRET_KEY` 弱（默认值或 < 32 字节）时会**拒绝启动**。生成与配置方式见 [安全配置](docs/DEPLOYMENT.md#安全配置生产必读)。

项目剩余工作见 [收尾计划](docs/REMAINING_WORK.md)。

## 主要接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 进程存活 |
| `/ready` | GET | 依赖健康检查（MySQL、Qdrant、ES、Embedding） |
| `/debug` | GET | 调试页面，可提问并查看 SSE 执行过程 |
| `/api/query` | POST | SSE 流式问数 |
| `/api/query/sync` | POST | 同步问数，返回完整结果 |

## 技术栈

- Python 3.12 + FastAPI
- LangGraph（代理编排）
- MySQL 8.0（业务库 + 元数据库）
- Qdrant（列/指标向量召回）
- Elasticsearch + IK（字段取值召回）
- text-embeddings-inference（bge-large-zh-v1.5）
- OpenAI 兼容 LLM

## 验收

```bash
uv lock --check
uv run python -m compileall -q app tests
uv run pytest -q
$env:RUN_AUDIO_DATA_ACCEPTANCE='1'; uv run pytest -q -m integration
```

---

_原 README 中的设计笔记已迁移到 [docs/REMAINING_WORK.md](docs/REMAINING_WORK.md) 和代码注释中。_
