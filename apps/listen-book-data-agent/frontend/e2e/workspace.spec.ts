import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * E2E uses scripted SSE through page.route — no backend required.
 * `vite preview` serves the production build via playwright.config webServer.
 */

function sse(events: Array<Record<string, unknown>>): string {
  return events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join('');
}

function sseHandler(events: Array<Record<string, unknown>>) {
  return (route: Route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: sse(events),
    });
}

const READY = {
  status: 'ready',
  dependencies: {
    metadata_mysql: { status: 'ok' },
    warehouse_mysql: { status: 'ok' },
    qdrant: { status: 'ok' },
  },
};

const DETAIL_FLOW = [
  { type: 'context', request_id: 'e2e-detail' },
  { type: 'progress', step: '分析问题', status: 'running' },
  { type: 'progress', step: '分析问题', status: 'success' },
  {
    type: 'context',
    request_id: 'e2e-detail',
    analysis_plan: {
      intent: 'detail',
      metric_hints: [],
      dimensions: ['专辑'],
      filters: [],
      time_range: { start: null, end: null, label: null },
      time_grain: null,
      top_n: 2,
      sort_direction: null,
      comparison: null,
    },
    tables: ['dw_album'],
    warnings: [],
  },
  { type: 'progress', step: '生成SQL', status: 'success' },
  { type: 'sql', sql: 'select name, category from dw_album limit 2', status: 'validated' },
  {
    type: 'result',
    data: [
      { name: '三体', category: '科幻' },
      { name: '红楼梦', category: '文学' },
    ],
    sql: 'select name, category from dw_album limit 2',
    columns: ['name', 'category'],
    row_count: 2,
    truncated: false,
  },
  {
    type: 'answer',
    summary: '已执行查询，共返回 2 行。',
    row_count: 2,
    columns: ['name', 'category'],
    metrics: [],
    time_range: '未限定',
    sql: 'select name, category from dw_album limit 2',
  },
  { type: 'done', status: 'completed', duration_ms: 480, error: null },
];

const TREND_FLOW = [
  { type: 'context', request_id: 'e2e-trend' },
  { type: 'sql', sql: 'select dt, cnt from t', status: 'validated' },
  {
    type: 'result',
    data: [
      { 日期: '2026-07-10', 播放量: 120 },
      { 日期: '2026-07-11', 播放量: 156 },
      { 日期: '2026-07-12', 播放量: 98 },
      { 日期: '2026-07-13', 播放量: 201 },
    ],
    sql: 'select dt, cnt from t',
    columns: ['日期', '播放量'],
    row_count: 4,
    truncated: false,
  },
  {
    type: 'answer',
    summary: '已执行查询，共返回 4 行。',
    row_count: 4,
    columns: ['日期', '播放量'],
    metrics: ['播放量'],
    time_range: '最近7天',
    sql: 'select dt, cnt from t',
  },
  {
    type: 'visualization',
    chart_spec: {
      schema_version: 'chart-spec/v1',
      type: 'line',
      title: '播放量趋势',
      dimension: '日期',
      metrics: ['播放量'],
      series: null,
      source: 'deterministic',
    },
  },
  { type: 'done', status: 'completed', duration_ms: 620, error: null },
];

const RANK_FLOW = [
  { type: 'context', request_id: 'e2e-rank' },
  {
    type: 'sql',
    sql: 'select name, plays from t order by plays desc limit 3',
    status: 'validated',
  },
  {
    type: 'result',
    data: [
      { 专辑名称: '三体', 播放量: 1024 },
      { 专辑名称: '明朝那些事儿', 播放量: 986 },
      { 专辑名称: '红楼梦', 播放量: 877 },
    ],
    sql: 'select name, plays from t order by plays desc limit 3',
    columns: ['专辑名称', '播放量'],
    row_count: 3,
    truncated: false,
  },
  {
    type: 'visualization',
    chart_spec: {
      schema_version: 'chart-spec/v1',
      type: 'bar',
      title: '专辑播放量排行',
      dimension: '专辑名称',
      metrics: ['播放量'],
      series: null,
      source: 'deterministic',
    },
  },
  { type: 'done', status: 'completed', duration_ms: 510, error: null },
];

const E2E_USER = {
  id: 'e2e-user',
  username: 'tester',
  role: 'user',
  must_change_password: false,
};

/** 模拟已登录会话：预置令牌并接管 /api/auth/* 接口。 */
async function stubAuth(page: Page) {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(E2E_USER),
    }),
  );
  await page.addInitScript(() => {
    window.localStorage.setItem('listenbook-auth-token', 'e2e-token');
  });
}

async function stubBackend(page: Page, events: Array<Record<string, unknown>>) {
  await page.route('**/ready', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(READY),
    }),
  );
  await page.route('**/api/query', sseHandler(events));
  await page.route('**/api/conversations', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({
        status: 201,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: 'e2e-conversation',
          title: 'E2E 会话',
          status: 'active',
          created_at: '2026-07-19T10:00:00',
          updated_at: '2026-07-19T10:00:00',
        }),
      });
    }
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: '[]',
    });
  });
  await page.route('**/api/conversations/**', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: '[]',
    }),
  );
  await page.route('**/api/traces', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: '[]',
    }),
  );
  await page.route('**/api/insight-cards', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: '[]',
    }),
  );
}

test.beforeEach(async ({ page }) => {
  await stubAuth(page);
  await stubBackend(page, DETAIL_FLOW);
});

test('完整问数流程：解释、表格、SQL、时间线、请求 ID', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('列出两个专辑');
  await page.getByRole('button', { name: '执行查询' }).click();

  await expect(page.getByText('已执行查询，共返回 2 行。')).toBeVisible();
  await expect(page.getByRole('table', { name: '查询结果表格' })).toBeVisible();
  await expect(page.getByRole('cell', { name: '三体' })).toBeVisible();

  // 检查器：时间线、数据表、请求 ID
  await expect(page.getByText('e2e-detail')).toBeVisible();
  await expect(page.getByText('dw_album', { exact: true })).toBeVisible();
  await expect(page.getByText('分析问题')).toBeVisible();

  // SQL 默认折叠，展开后可复制
  await page.getByRole('button', { name: /查看 SQL/ }).click();
  await expect(page.getByText(/select name, category from dw_album/)).toBeVisible();
});

test('时间加数值列渲染折线图', async ({ page }) => {
  await stubBackend(page, TREND_FLOW);
  await page.goto('/');
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('播放趋势');
  await page.getByRole('button', { name: '执行查询' }).click();

  await expect(page.getByRole('heading', { name: '播放量趋势' })).toBeVisible();
  await expect(page.locator('.chart-panel svg').first()).toBeVisible();
  await expect(page.getByRole('table', { name: '查询结果表格' })).toBeVisible();
});

test('文本维度加数值列渲染横向柱状图', async ({ page }) => {
  await stubBackend(page, RANK_FLOW);
  await page.goto('/');
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('专辑排行');
  await page.getByRole('button', { name: '执行查询' }).click();

  await expect(page.getByRole('heading', { name: '专辑播放量排行' })).toBeVisible();
  await expect(page.locator('.chart-panel svg').first()).toBeVisible();
});

test('键盘操作：Ctrl+Enter 提交查询', async ({ page }) => {
  await page.goto('/');
  const input = page.getByRole('textbox', { name: '自然语言问题' });
  await input.click();
  await input.fill('键盘提交');
  await page.keyboard.press('Control+Enter');
  await expect(page.getByText('已执行查询，共返回 2 行。')).toBeVisible();
});

test('多轮查询携带当前会话与父 Trace', async ({ page }) => {
  const queryBodies: Array<Record<string, unknown>> = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/query' && request.postData()) {
      queryBodies.push(request.postDataJSON() as Record<string, unknown>);
    }
  });
  await page.goto('/');
  const input = page.getByRole('textbox', { name: '自然语言问题' });
  await input.fill('最近7天播放趋势');
  await page.getByRole('button', { name: '执行查询' }).click();
  await expect(page.getByText('已执行查询，共返回 2 行。')).toBeVisible();

  await input.fill('那上个月呢');
  await page.getByRole('button', { name: '执行查询' }).click();
  await expect.poll(() => queryBodies.length).toBe(2);
  expect(queryBodies[1]).toEqual({
    query: '那上个月呢',
    conversation_id: 'e2e-conversation',
    parent_trace_id: 'e2e-detail',
  });
});

test('洞察卡片重新打开时走当前权限执行接口', async ({ page }) => {
  const card = {
    id: 'e2e-card',
    question: '列出两个专辑',
    answer_summary: '已执行查询，共返回 2 行。',
    sql_template: 'SELECT name FROM dw_album LIMIT :p1',
    parameter_types: ['integer'],
    chart_spec: {
      schema_version: 'chart-spec/v1',
      type: 'table',
      title: '专辑列表',
      dimension: null,
      metrics: [],
      series: null,
      source: 'deterministic',
    },
    version_info: { build_id: 'build-e2e' },
    created_at: '2026-07-19T10:00:00',
  };
  let cards: (typeof card)[] = [];
  const executeBodies: Array<Record<string, unknown>> = [];
  await page.unroute('**/api/insight-cards');
  await page.route('**/api/insight-cards', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cards),
    }),
  );
  await page.route('**/api/insight-cards/from-trace/**', (route) => {
    cards = [card];
    return route.fulfill({
      status: 201,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(card),
    });
  });
  await page.route('**/api/insight-cards/e2e-card/execute', (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    executeBodies.push(body);
    return sseHandler(DETAIL_FLOW)(route);
  });

  await page.goto('/');
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('列出两个专辑');
  await page.getByRole('button', { name: '执行查询' }).click();
  await expect(page.getByText('已执行查询，共返回 2 行。')).toBeVisible();
  await page.getByRole('button', { name: '保存洞察卡片' }).click();
  await expect(page.getByText('洞察卡片已保存')).toBeVisible();
  await page.getByRole('button', { name: '重新鉴权打开' }).click();

  await expect.poll(() => executeBodies.length).toBe(1);
  expect(executeBodies[0]).toEqual({
    conversation_id: 'e2e-conversation',
    parent_trace_id: 'e2e-detail',
  });
});

test('取消查询后展示取消状态', async ({ page }) => {
  await page.unroute('**/api/query');
  await page.route('**/api/query', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 5000));
    try {
      await route.fulfill({ status: 200, body: '' });
    } catch {
      // 客户端已取消，忽略
    }
  });
  await page.goto('/');
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('慢查询');
  await page.getByRole('button', { name: '执行查询' }).click();
  await page.getByRole('button', { name: '取消查询' }).click();
  await expect(page.getByText('查询已取消', { exact: true })).toBeVisible();
});

test('平板布局：检查器变为抽屉', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.goto('/');
  // 检查器默认在屏外
  await expect(page.getByText('e2e-detail')).not.toBeVisible();
  await page.getByRole('button', { name: '打开检查器' }).click();
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('列出两个专辑');
  await page.getByRole('button', { name: '执行查询' }).click();
  await expect(page.getByText('e2e-detail')).toBeVisible();
  await page.keyboard.press('Escape');
});

test('移动端布局：单列标签页且无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 720 });
  await page.goto('/');
  await expect(page.getByRole('tab', { name: '示例问题' })).toBeVisible();

  // 切换到示例问题标签并执行
  await page.getByRole('tab', { name: '示例问题' }).click();
  await page.getByRole('button', { name: '内容' }).click();
  await page.getByRole('button', { name: '平台一共有多少个有声专辑' }).click();
  // 点击示例自动切回结果标签
  await expect(page.getByText('已执行查询，共返回 2 行。')).toBeVisible();

  // 执行详情标签展示检查器
  await page.getByRole('tab', { name: '执行详情' }).click();
  await expect(page.getByText('e2e-detail')).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test('桌面端无横向页面溢出', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.getByRole('textbox', { name: '自然语言问题' }).fill('列出两个专辑');
  await page.getByRole('button', { name: '执行查询' }).click();
  await expect(page.getByText('已执行查询，共返回 2 行。')).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
