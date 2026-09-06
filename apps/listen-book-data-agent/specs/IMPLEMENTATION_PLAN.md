## 实施目标

在当前仓库完成 PRD 全部功能，覆盖 `audio-data` 的 54 张表。保留 SSE 查询，新增同步 API、调试页、完整指标语义、SQL 安全、查询追踪、自动化测试和部署文档。

所有功能直接提交到 `main`，每个功能点完成验证后执行一次：

`git commit` → `git push origin main`

不使用强制推送；若远程 `main` 出现新提交或冲突，停止推送并先确认，不擅自覆盖。

## 分功能提交顺序

| 次序 | 提交信息                                                     | 功能与提交前验证                                             |
| ---- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 1    | `docs: add repository guidelines and product requirements`   | 提交现有 `AGENTS.md` 与 `specs/PRD.md`，检查 Markdown        |
| 2    | `feat(data): add self-contained audio data bootstrap`        | 迁入 DDL、种子、生成器；支持 smoke/full 和显式 `--reset`；验证 54 张表 |
| 3    | `feat(infra): add local service infrastructure and configuration` | 添加 MySQL、Qdrant、ES+IK、Embedding Compose、环境变量示例和 OpenAI-compatible 配置 |
| 4    | `feat(metadata): model audio schema relationships and sensitivity` | 扩展元数据模型，覆盖全部表、中文别名、外键、多态关系及 PII 标记 |
| 5    | `feat(metrics): add audiobook analytics metric catalog`      | 实现内容、播放、互动、会员、订单、退款、搜索、推荐和排行指标，并与基准 SQL 对照 |
| 6    | `feat(knowledge): add versioned semantic knowledge builder`  | 实现元数据、Qdrant、ES 的幂等构建、版本记录和失败回滚        |
| 7    | `feat(agent): add structured analysis planning and retrieval` | 实现意图、指标、维度、时间、Top N 解析，关系路径补齐和召回降级 |
| 8    | `feat(sql): enforce safe read-only query execution`          | 引入 `sqlglot`，限制单条 SELECT、授权表、敏感列、500 行和 30 秒超时；纠错后重新校验 |
| 9    | `feat(answer): add grounded result explanations`             | 根据真实 SQL、指标、时间范围和返回数据生成解释，避免虚构     |
| 10   | `feat(observability): persist query traces and dependency health` | 保存请求、SQL、阶段耗时和错误但不保存结果行；增加 `/health`、`/ready` |
| 11   | `feat(api): add synchronous query API and reliable SSE events` | 保留 `/api/query`，新增 `/api/query/sync`，统一请求 ID、错误和终止事件 |
| 12   | `feat(ui): add built-in query debug page`                    | 增加 `/debug`，展示执行进度、SQL、结果表格、解释和错误       |
| 13   | `test: add end-to-end audiobook query coverage`              | Fake LLM 自动化测试、smoke 集成测试、PII/SQL 攻击测试及 full 数据验收 |
| 14   | `docs: add deployment and acceptance runbook`                | 完成初始化、配置、运行、知识库重建、故障排查和验收文档       |

每个功能提交会包含对应测试，不把未经验证的半成品推到 Gitee。

## 固定接口与安全标准

- `POST /api/query`：SSE 事件为 `progress`、`context`、`sql`、`result`、`answer`、`error`、`done`。
- `POST /api/query/sync`：返回请求 ID、SQL、列、数据行、行数、截断标记、指标、时间范围、解释及耗时。
- V1 不增加登录系统，但手机号、邮箱、消息正文等 PII 不进入知识库和 SQL 白名单。
- 使用只读 MySQL 账号；禁止 DDL、DML、多语句、系统库和未授权表。
- 窗口统计以事实表为准，专辑缓存计数只用于当前快照。
- 不合并原 `audio-data` 业务 FastAPI，不提交密钥、模型权重、数据卷或查询结果。

## 最终验收

- `uv lock --check`、编译检查和全部 `pytest` 通过。
- smoke 数据用于逐功能验证，full 数据用于最终验收。
- 覆盖专辑/声音、播放完成率、评论评分、收藏、会员、订单退款、搜索点击率、趋势、对比和 Top 排行。
- 使用本地 OpenAI-compatible 配置完成真实模型闭环。
- 最终确认本地 `main`、`origin/main` 一致且工作区无遗漏文件。