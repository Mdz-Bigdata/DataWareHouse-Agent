- # 听书问数前端实施计划

  ## 总体方案

  建设面向内部运营人员的正式问数工作台，替代现有原生 `/debug` 页面作为主要入口。

  默认决策：

  - 产品范围：问数工作台，不包含元数据管理后台和登录系统。
  - 交互形态：分析工作台，不采用纯聊天界面。
  - 技术栈：React、TypeScript、Vite、Carbon Design System、Carbon Charts。
  - 部署方式：前端独立构建，通过 Nginx 同域代理 FastAPI，避免 CORS。
  - 访问范围：内部网络、VPN或网关白名单。
  - 设计参数：`DESIGN_VARIANCE 3`、`MOTION_INTENSITY 2`、`VISUAL_DENSITY 7`，专业、高密度、低动效。
  - 保留现有绿色强调色，默认浅色主题，支持整页深色切换。

  ## 页面与交互

  桌面端采用三栏工作区：

  - 左侧：新建分析、按内容/播放/用户/交易/搜索推荐分类的示例问题、当前会话查询记录。
  - 中间：问题输入、执行状态、结果解释、指标卡、图表和数据表格。
  - 右侧检查器：分析计划、命中指标、涉及数据表、已校验 SQL、执行时间线和请求 ID。
  - 1024px 以下将右侧检查器改为抽屉，移动端改为单列标签页。

  结果展示规则：

  - 单行单数值显示核心指标。
  - 时间列加数值列显示折线图。
  - 文本维度加数值列显示横向柱状图。
  - 无法可靠判断时只显示表格，不猜测图表含义。
  - 表格支持排序、20/50/100 行分页、复制和安全 CSV 导出。
  - 截断结果明确显示“最多返回 500 行”。
  - SQL 默认折叠，允许复制但不能编辑执行。

  完整覆盖空状态、加载骨架、查询中、成功、无数据、截断、失败、取消和重试状态。执行进度使用 `aria-live`，所有操作支持键盘。

  ## 接口与状态模型

  现有后端接口保持不变：

  - `POST /api/query` 作为主要 SSE 数据源。
  - `POST /api/query/sync` 保留给测试和外部集成。
  - `GET /ready` 展示真实依赖健康状态。
  - `/debug` 暂时保留为故障排查入口。

  前端建立 TypeScript 判别联合类型，覆盖：

  ```
  progress | context | sql | result | answer | error | done
  ```

  查询生命周期固定为：

  ```
  idle → connecting → streaming → completed
                           ├→ failed
                           └→ cancelled
  ```

  使用 `fetch + ReadableStream + AbortController` 处理 POST SSE，支持分块帧、`\r\n`、网络中断和缺少 `done` 的异常情况。所有事件按照 `request_id` 归并，禁止旧请求覆盖新请求。

  查询记录只保存在当前浏览器内存，不持久化问题、结果行或 SQL。React 默认转义所有内容，禁止 `dangerouslySetInnerHTML`；CSV 导出防止公式注入。

  ## 工程与部署

  在仓库新增独立 `frontend/` 工程：

  - React 组件负责工作区、查询输入、结果表格、图表和执行检查器。
  - 使用 reducer 管理单次查询状态，不额外引入全局状态库。
  - 使用 Carbon 组件和图表，不混用其他设计系统。
  - 中文字体使用本地系统字体栈，不加载外部字体。

  新增前端多阶段 Docker 构建和 Nginx 配置：

  - `/` 返回 React SPA。
  - `/api/`、`/health`、`/ready`、`/debug` 反向代理到 FastAPI。
  - SSE 路由关闭代理缓冲，读取超时设置为 300 秒。
  - 静态哈希资源长期缓存，`index.html` 禁止长期缓存。
  - 生产环境只暴露 Nginx，FastAPI 端口保留在 Docker 内部网络。
  - 配置 HTTPS、CSP、`X-Content-Type-Options` 和网关访问限制。

  ## 实施提交顺序

  每项验证后提交并推送 Gitee `main`：

  1. `feat(frontend): scaffold analytics workspace`
  2. `feat(frontend): integrate streaming query lifecycle`
  3. `feat(frontend): add result tables and charts`
  4. `feat(frontend): add query inspector and accessibility states`
  5. `feat(infra): serve frontend through same-origin proxy`
  6. `test(frontend): add unit and browser coverage`
  7. `docs(frontend): add development and deployment guide`

  不删除现有 `/debug`，待正式工作台通过验收后再单独决定是否下线。

  ## 测试与验收

  自动化测试包括：

  - Vitest：SSE 分帧、状态 reducer、图表识别、CSV 安全、格式化函数。
  - React Testing Library：输入校验、执行、取消、重试、空结果、错误和截断提示。
  - Playwright：模拟完整 SSE 流，验证表格、图表、SQL、解释、响应式布局和键盘操作。
  - 真实联调：使用 `audio_full` 跑 PRD 的 10 类问数问题。

  交付验收命令：

  ```
  cd frontend
  npm ci
  npm run typecheck
  npm run test
  npm run build
  npm run e2e
  
  cd ..
  uv run pytest -q
  docker compose -f infra/compose.app.yaml config
  ```

  验收标准：

  - SSE 首个进度事件能够立即展示，取消查询后不再接收结果。
  - PRD 10 类问题均能展示解释、SQL和结构化结果。
  - 500 行结果操作流畅，图表不修改或重新聚合原始数据。
  - 桌面、平板和移动端无横向页面溢出，表格区域允许独立滚动。
  - 前端与 API 使用同域访问，不开放通配 CORS。
  - Lighthouse 可访问性不低于 90，主要文字和控件达到 WCAG AA。
