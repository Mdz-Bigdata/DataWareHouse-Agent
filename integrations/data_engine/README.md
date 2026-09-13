# 确定性 Data Agent Engine 集成

该目录把 `apps/data-agent-engine` 的原生 DSH/MCP 引擎接入统一门户，不修改固定的
上游源码快照。适配器将 20 个 MCP 工具映射为受服务令牌保护的 HTTP API，统一网关
在服务端注入令牌，浏览器端不会获取凭据。

## 启动

```bash
./platform.sh init
./platform.sh up-data-engine
```

也可运行 `./start.sh` 启动核心、Data Engine 与两套 NanZi 平台，或运行
`./platform.sh up-full` 启动全部能力。进入主门户顶部的“确定性引擎”工作台。

## 安全与写操作

- 查询链必须依次取得 `confirm_token` 和 `query_token`；探索链使用独立的
  `explore_token`。令牌都与 MQL/SQL 指纹绑定。
- HTTP 写操作默认关闭。可信环境需要本体注册或调度提交时，在私有
  `.env.platform` 设置 `DATA_ENGINE_ALLOW_HTTP_MUTATIONS=true` 后重建服务。
- 本体数据和审计日志分别保存在 `data-engine-ontology`、`data-engine-logs`
  持久卷；SQLite 样例库由镜像按固定种子生成，不纳入 Git。
- `scheduler_submit` 在适配层桥接到 NanZi 智能体任务中心，使用幂等键避免重复
  创建。只启动 Data Engine 而未启动 `agents` 服务时，调度提交会返回结构化错误。

真实数仓和 Supabase 本体仓的配置项沿用上游
[`backend/.env.example`](../../apps/data-agent-engine/backend/.env.example)。完整需求映射和
验证基线见 [`docs/source-integrations/data-agent-engine.md`](../../docs/source-integrations/data-agent-engine.md)。
