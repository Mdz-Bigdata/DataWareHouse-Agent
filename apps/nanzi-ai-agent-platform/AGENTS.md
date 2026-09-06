## Git 协作：用户说「代码已合」

用户表示 PR/代码已合并后，**必须立即执行本地 git 同步**，不要只口头确认：

1. `git fetch origin`
2. `git checkout main && git pull origin main`
3. `git checkout dev-agentscope && git merge main`（或 `git rebase main`，以 `DEVELOPMENT.md` 为准）
4. `git push origin dev-agentscope`（若本地领先远程）
5. 汇报：`main` / `dev-agentscope` 最新 commit、是否与远程一致
6. 提醒用户自行在控制台执行 `./dev.sh`（Agent **不**代跑）

**不要**在用户未要求时擅自创建 PR 或 force push。

## Git 协作：创建 / 更新 Pull Request

用户要求创建或更新 PR 时，**必须**按仓库根目录 [`PULL_REQUEST_TEMPLATE.md`](./PULL_REQUEST_TEMPLATE.md) 填写标题与正文（概要、核心变更、Commit Log、测试覆盖、备注），不要使用默认的 Summary/Test plan 两段式。提交前同步更新 `tests/CHECKLIST.md`，并在 PR 中注明。

## 开发环境服务启停

**任何场景下**均严禁主动或自动运行 `./dev.sh` 等编译、部署与启动脚本（包括用户说「代码已合」时）。所有的服务编译、启停和重启测试均需交由用户在控制台手动操作。修改完毕或同步完成后，Agent 仅负责通知用户代码/分支已就绪，并提醒用户自行执行 `./dev.sh`。
