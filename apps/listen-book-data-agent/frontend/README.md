# 听书问数工作台（前端）

面向内部运营人员的正式问数工作台，替代原生 `/debug` 页面作为主要入口。
技术栈：React 18 + TypeScript + Vite + Carbon Design System + Carbon Charts。

## 功能概览

- 三栏工作区：左侧示例问题与查询记录，中间提问与结果，右侧执行检查器
  （分析计划、命中指标、涉及数据表、已校验 SQL、执行时间线、请求 ID）。
- 通过 `POST /api/query`（SSE）流式展示执行进度，支持取消、失败重试、
  网络中断与缺失 `done` 事件的兜底处理；旧请求事件不会覆盖新请求。
- 结果展示规则：单行单数值 → 核心指标卡；时间列 + 数值列 → 折线图；
  文本维度 + 数值列 → 横向柱状图（超过 30 个类目退回表格）；其余只显示表格。
  图表只映射原始数据，不修改、不重新聚合。
- 数据表格支持排序、20/50/100 行分页、复制和安全 CSV 导出（防公式注入）。
- 截断结果明确提示“最多返回 500 行”；SQL 默认折叠、可复制、只读。
- 查询记录只保存在浏览器内存中，刷新即清空。
- 浅色主题默认，支持整页深色切换（仅主题偏好写入 localStorage）。
- 响应式：≤1023px 检查器改为抽屉，≤671px 单列标签页；`aria-live`
  播报执行进度，全部操作可键盘完成。

## 本地开发

```bash
npm ci
npm run dev        # http://localhost:5173，/api 等路径代理到 http://localhost:8000
```

开发前先在仓库根目录启动后端：

```bash
uv run uvicorn main:app --reload
```

## 验证命令

```bash
npm run typecheck  # TypeScript 严格检查
npm run test       # Vitest 单元测试 + React Testing Library 组件测试
npm run build      # 生产构建（输出 dist/）
npm run e2e        # Playwright 端到端测试（需先 npm run build；
                   # 首次运行前执行 npx playwright install chromium）
```

## 目录结构

```
src/
  types/events.ts        # SSE 事件判别联合类型（progress|context|sql|result|answer|error|done）
  lib/sse.ts             # SSE 分帧读取（跨块帧、CRLF、尾帧容错）
  lib/queryClient.ts     # POST /api/query 流式客户端（AbortController）
  lib/chartDetection.ts  # 结果可视化规则判定
  lib/csv.ts             # CSV 生成与公式注入防护
  lib/sort.ts            # 表格数值感知排序
  state/queryReducer.ts  # 单次查询生命周期 idle→connecting→streaming→completed/failed/cancelled
  state/historyReducer.ts# 会话查询记录（仅内存）
  state/useQueryController.ts # reducer 与客户端的编排（request_id 防串扰）
  components/            # 工作区组件
  test/                  # Vitest + RTL 测试
e2e/                     # Playwright 测试（page.route 模拟 SSE）
```

## 部署

前端独立构建为静态资源，由 Nginx 同域托管并反向代理 FastAPI（避免 CORS）：

```bash
docker compose -f infra/compose.app.yaml up -d --build
# 工作台：http://localhost:8080/（WEB_PORT 可改）
```

- `/` 返回 React SPA；`/api/`、`/health`、`/ready`、`/debug` 代理到 FastAPI。
- `/api/` 关闭代理缓冲、读写超时 300 秒，保证 SSE 进度事件实时到达。
- `/assets/` 指纹资源长期缓存，`index.html` 禁止长期缓存。
- 生产环境只暴露 Nginx，FastAPI 端口留在 Docker 内部网络。
- 默认监听 80（HTTP 由网关终止 TLS）；`nginx.conf` 中保留了 HTTPS 与
  证书挂载的注释模板，可直接启用。
