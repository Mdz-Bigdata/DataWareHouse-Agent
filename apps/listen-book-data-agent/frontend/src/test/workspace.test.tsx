import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';

vi.mock('@carbon/charts-react', () => {
  const StubChart = () => <svg aria-label="测试图表" viewBox="0 0 800 400" />;
  return {
    GroupedBarChart: StubChart,
    LineChart: StubChart,
    PieChart: StubChart,
    SimpleBarChart: StubChart,
  };
});

const AUTH = {
  user: { id: 'u1', username: 'tester', role: 'user' as const, must_change_password: false },
  onLogout: () => {},
  onChangePassword: async () => {},
};

function sseText(events: Array<Record<string, unknown>>): string {
  return events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join('');
}

function sseResponse(events: Array<Record<string, unknown>>): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(sseText(events)));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

const READY_BODY = {
  status: 'ready',
  dependencies: {
    metadata_mysql: { status: 'ok' },
    warehouse_mysql: { status: 'ok' },
  },
};

/** Stub fetch: health + conversation CRUD + scripted query SSE. */
function stubFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  let conversationCreated = false;
  let insightCards: Array<Record<string, unknown>> = [];
  const conversation = {
    id: 'conversation-test',
    title: '测试会话',
    status: 'active',
    created_at: '2026-07-19T10:00:00',
    updated_at: '2026-07-19T10:00:00',
  };
  const jsonResponse = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  const mock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = String(url);
    if (path.includes('/ready')) {
      return jsonResponse(READY_BODY);
    }
    if (path.includes('/api/conversations')) {
      if (path.endsWith('/turns')) return jsonResponse([]);
      if (init?.method === 'POST') {
        conversationCreated = true;
        return jsonResponse(conversation, 201);
      }
      if (init?.method === 'PATCH') return jsonResponse(conversation);
      return jsonResponse(conversationCreated ? [conversation] : []);
    }
    if (path.endsWith('/analysis') || path.endsWith('/execute')) {
      return handler(path, init);
    }
    if (path.startsWith('/api/insight-cards/from-trace/') && init?.method === 'POST') {
      const card = {
        id: 'card-test',
        question: '平台一共有多少个有声专辑',
        answer_summary: '已执行查询，共返回 2 行。',
        sql_template: 'SELECT name FROM dw_album LIMIT :p1',
        parameter_types: ['integer'],
        chart_spec: { schema_version: 'chart-spec/v1', type: 'table' },
        version_info: { build_id: 'build-2026-07' },
        created_at: '2026-07-19T10:00:00',
      };
      insightCards = [card];
      return jsonResponse(card, 201);
    }
    if (path.startsWith('/api/insight-cards/') && init?.method === 'DELETE') {
      insightCards = insightCards.filter((item) => item.id !== path.split('/').pop());
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (path === '/api/insight-cards') {
      return jsonResponse(insightCards);
    }
    if (path.includes('/api/traces')) {
      return jsonResponse([]);
    }
    return handler(path, init);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

function stubRestoredConversation() {
  const baseConversation = {
    id: 'conversation-restored',
    title: '播放趋势会话',
    status: 'active',
    created_at: '2026-07-19T10:00:00',
    updated_at: '2026-07-19T10:10:00',
  };
  const otherConversation = {
    ...baseConversation,
    id: 'conversation-other',
    title: '订单分析会话',
  };
  const turn = {
    id: 'trace-restored',
    query_text: '最近7天播放趋势',
    standalone_question: '统计最近7天按天播放次数趋势',
    status: 'completed',
    total_duration_ms: 120,
    started_at: '2026-07-19T10:00:00',
    completed_at: '2026-07-19T10:00:01',
    parent_trace_id: null,
    regenerate_of_trace_id: null,
    query_plan_summary: {
      intent: 'trend',
      metric_hints: ['播放次数'],
      dimensions: [],
      filters: [],
      time_range: { start: '2026-07-13', end: '2026-07-19', label: '最近7天' },
      time_grain: 'day',
      top_n: null,
      sort_direction: null,
      comparison: null,
    },
    answer_summary: '历史摘要：播放次数整体上升。',
    chart_spec: { type: 'line' },
    sql: 'SELECT play_date, COUNT(*) FROM play_session GROUP BY play_date',
    build_id: 'build-1',
    policy_version: 'policy-v1',
    policy_hash: 'hash-1',
  };
  let conversations = [baseConversation, otherConversation];
  const response = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  const mock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = String(url);
    if (path.includes('/ready')) return response(READY_BODY);
    if (path.includes('/api/traces/trace-restored/regenerate')) {
      return Promise.resolve(sseResponse(TABLE_FLOW));
    }
    if (path === '/api/query') return Promise.resolve(sseResponse(TABLE_FLOW));
    if (path.endsWith('/turns')) return response([turn]);
    if (path.startsWith('/api/conversations/') && init?.method === 'PATCH') {
      const id = path.split('/').pop();
      const patch = JSON.parse(String(init.body)) as { title?: string; status?: string };
      const existing = conversations.find((item) => item.id === id) ?? baseConversation;
      const updated = { ...existing, ...patch };
      conversations =
        patch.status === 'archived'
          ? conversations.filter((item) => item.id !== id)
          : conversations.map((item) => (item.id === id ? updated : item));
      return response(updated);
    }
    if (path.startsWith('/api/conversations')) return response(conversations);
    return response([]);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

const TABLE_FLOW = [
  { type: 'context', request_id: 'req-table' },
  { type: 'progress', step: '分析问题', status: 'running' },
  { type: 'progress', step: '分析问题', status: 'success' },
  {
    type: 'context',
    request_id: 'req-table',
    analysis_plan: {
      intent: 'detail',
      metric_hints: [],
      dimensions: ['专辑'],
      filters: [],
      time_range: { start: null, end: null, label: null },
      time_grain: null,
      top_n: null,
      sort_direction: null,
      comparison: null,
    },
    query_plan: {
      schema_version: 'query-plan/v1',
      intent: 'detail',
      complexity: 'EASY',
      metrics: [{ semantic_id: 'album_count', label: '专辑数' }],
      dimensions: [{ semantic_id: 'audio_album.album_name', label: '专辑' }],
      filters: [],
      time: { field_id: null, start: null, end: null, label: null, grain: null },
      sort: [],
      join_path: ['album_category'],
      subplans: [],
      limit: 2,
      comparison: null,
      source_hints: {},
    },
    tables: ['dw_album', 'dw_category'],
    build_id: 'build-2026-07',
    policy_version: 'policy-v3',
    policy_hash: '1234567890abcdef1234567890abcdef',
    policy_admin_bypass: false,
    semantic_release_id: 'release-3',
    semantic_release_version: 3,
    query_set_id: 'query-set-4',
    query_set_version: 4,
    business_rule_set_id: 'rule-set-2',
    business_rule_set_version: 2,
    semantic_term_matches: [
      {
        term_key: 'audio_album',
        standard_term: '有声专辑',
        version: 2,
        bindings: [{ kind: 'table', semantic_id: 'audio_album' }],
      },
    ],
    verified_query_examples: [
      { case_key: 'album_detail', revision_id: 'revision-1', similarity: 0.88 },
    ],
    business_rule_matches: [
      { rule_key: 'published_only', version: 3, rule_type: 'filter_constraint' },
    ],
    raw_prompt: 'SECRET_PROMPT_SHOULD_NOT_RENDER',
    api_key: 'SECRET_KEY_SHOULD_NOT_RENDER',
    result_rows: [{ private_value: 'SECRET_ROW_SHOULD_NOT_RENDER' }],
    warnings: [],
  },
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
  {
    type: 'visualization',
    chart_spec: {
      schema_version: 'chart-spec/v1',
      type: 'table',
      title: '数据表格',
      dimension: null,
      metrics: [],
      series: null,
      source: 'deterministic',
    },
  },
  {
    type: 'done',
    status: 'completed',
    duration_ms: 640,
    error: null,
    llm_calls: 2,
    token_usage: { input_tokens: 120, output_tokens: 30, total_tokens: 150, available: true },
  },
];

async function typeAndRun(user: ReturnType<typeof userEvent.setup>, question: string) {
  const input = screen.getByRole('textbox', { name: '自然语言问题' });
  await user.type(input, question);
  await user.click(screen.getByRole('button', { name: '执行查询' }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('听书问数工作台', () => {
  it('初始渲染：示例分类、空状态与检查器占位', async () => {
    stubFetch(() => sseResponse([]));
    render(<App auth={AUTH} />);
    expect(screen.getByRole('button', { name: '新建分析' })).toBeInTheDocument();
    expect(screen.getByText('输入问题，或从左侧选择示例问题开始分析。')).toBeInTheDocument();
    expect(screen.getByText('内容')).toBeInTheDocument();
    expect(screen.getByText('搜索推荐')).toBeInTheDocument();
    expect(screen.getByText('执行查询后展示各阶段进度。')).toBeInTheDocument();
    expect(await screen.findByText('服务正常')).toBeInTheDocument();
  });

  it('空问题校验：Ctrl+Enter 提示请输入问题', async () => {
    stubFetch(() => sseResponse([]));
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    const input = screen.getByRole('textbox', { name: '自然语言问题' });
    input.focus();
    await user.keyboard('{Control>}{Enter}{/Control}');
    expect(await screen.findByText('请输入问题后再执行查询。')).toBeInTheDocument();
  });

  it('完整流程：解释、表格、SQL、时间线、请求 ID 与查询记录', async () => {
    stubFetch(() => sseResponse(TABLE_FLOW));
    const user = userEvent.setup();
    render(<App auth={AUTH} />);

    await typeAndRun(user, '列出两个专辑');

    // 结果解释
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
    // 数据表格
    const table = screen.getByRole('table', { name: '查询结果表格' });
    expect(within(table).getByText('三体')).toBeInTheDocument();
    expect(within(table).getByText('红楼梦')).toBeInTheDocument();
    expect(screen.getByText('共 2 行')).toBeInTheDocument();
    // 检查器：时间线 / 数据表 / 请求 ID
    expect(screen.getByText('分析问题')).toBeInTheDocument();
    expect(screen.getByText('dw_album')).toBeInTheDocument();
    expect(screen.getByText('req-table')).toBeInTheDocument();
    // SQL 默认折叠，展开后可见且可复制
    await user.click(screen.getByRole('button', { name: /查看 SQL/ }));
    expect(screen.getByText(/select name, category from dw_album/)).toBeInTheDocument();
    // 查询记录新增一条已完成记录
    expect(
      await screen.findByRole('button', { name: /打开历史：列出两个专辑（已完成）/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '结果正确' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '结果有误' })).toBeInTheDocument();
    expect(screen.getByText('ChartSpecV1')).toBeInTheDocument();
    expect(screen.getByText('明细 / EASY')).toBeInTheDocument();
    expect(screen.getByText(/album_count/)).toBeInTheDocument();
    expect(screen.getByText('有声专辑 v2')).toBeInTheDocument();
    expect(screen.getByText('近似：album_detail')).toBeInTheDocument();
    expect(screen.getByText('published_only v3')).toBeInTheDocument();
    expect(screen.getByText(/v4/)).toBeInTheDocument();
    expect(screen.getByText(/150（输入 120，输出 30）/)).toBeInTheDocument();
    expect(screen.queryByText('SECRET_PROMPT_SHOULD_NOT_RENDER')).not.toBeInTheDocument();
    expect(screen.queryByText('SECRET_KEY_SHOULD_NOT_RENDER')).not.toBeInTheDocument();
    expect(screen.queryByText('SECRET_ROW_SHOULD_NOT_RENDER')).not.toBeInTheDocument();
  });

  it('错误反馈提交结构化原因并提示进入人工审核', async () => {
    const fetchMock = stubFetch(() => sseResponse(TABLE_FLOW));
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '列出两个专辑');

    await user.click(await screen.findByRole('button', { name: '结果有误' }));
    await user.selectOptions(screen.getByLabelText('原因'), 'wrong_join');
    await user.type(screen.getByLabelText('补充说明（可选）'), '专辑和分类关联不正确');
    await user.click(screen.getByRole('button', { name: '提交反馈' }));

    expect(await screen.findByText('已生成待人工审核的候选案例。')).toBeInTheDocument();
    const feedbackCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/api/traces/req-table/feedback'),
    );
    expect(feedbackCall).toBeDefined();
    expect(JSON.parse(String(feedbackCall?.[1]?.body))).toEqual({
      verdict: 'incorrect',
      reasons: ['wrong_join'],
      comment: '专辑和分类关联不正确',
    });
  });

  it('深入分析重新鉴权后分栏展示事实、推断和具体证据', async () => {
    const analysis = {
      trace_id: 'trace-analysis',
      source_trace_id: 'req-table',
      status: 'completed',
      facts: [
        {
          fact_id: 'fact-1',
          statement: '播放次数最大值为 20。',
          evidence_ids: ['evidence-1'],
        },
      ],
      inferences: [
        {
          inference_id: 'inference-1',
          statement: '各渠道之间存在明显差异。',
          fact_ids: ['fact-1'],
          confidence: 'medium',
        },
      ],
      evidence: [
        {
          evidence_id: 'evidence-1',
          description: '播放次数的有限结果统计',
          values: { minimum: '5', maximum: '20', observations: 2 },
        },
      ],
      rerun_row_count: 2,
      row_limit: 100,
      truncated: false,
      policy_version: 'policy-v2',
      policy_hash: 'hash-v2',
      build_id: 'build-v2',
      disclaimer: '推断仅基于本次重新鉴权后的有限结果，不包含未来预测。',
    };
    const fetchMock = stubFetch((url) =>
      url.endsWith('/analysis')
        ? new Response(JSON.stringify(analysis), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        : sseResponse(TABLE_FLOW),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '列出两个专辑');

    await user.click(await screen.findByRole('button', { name: '深入分析' }));

    expect(await screen.findByText('播放次数最大值为 20。')).toBeInTheDocument();
    expect(screen.getByText('各渠道之间存在明显差异。')).toBeInTheDocument();
    expect(screen.getByText(/当前权限 policy-v2/)).toBeInTheDocument();
    expect(screen.getByText(/不包含未来预测/)).toBeInTheDocument();
    await user.click(screen.getByText('查看具体证据（1 项）'));
    expect(screen.getByText(/minimum=5/)).toBeInTheDocument();
    const analysisCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/api/traces/req-table/analysis'),
    );
    expect(analysisCall?.[1]?.method).toBe('POST');
  });

  it('单行单数值结果显示核心指标卡', async () => {
    stubFetch(() =>
      sseResponse([
        { type: 'context', request_id: 'req-metric' },
        {
          type: 'result',
          data: [{ 退款金额: '12345.67' }],
          columns: ['退款金额'],
          row_count: 1,
          truncated: false,
        },
        {
          type: 'answer',
          summary: '已执行查询，返回 1 行。',
          row_count: 1,
          columns: ['退款金额'],
          metrics: ['退款金额'],
          time_range: '本月（2026-07-01 至 2026-07-17）',
          sql: 'select ...',
        },
        {
          type: 'visualization',
          chart_spec: {
            schema_version: 'chart-spec/v1',
            type: 'kpi',
            title: '退款金额',
            dimension: null,
            metrics: ['退款金额'],
            series: null,
            source: 'deterministic',
          },
        },
        { type: 'done', status: 'completed', duration_ms: 300, error: null },
      ]),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '本月退款金额是多少');
    expect(await screen.findByText('12,345.67')).toBeInTheDocument();
    expect(screen.getByLabelText('核心指标：退款金额')).toBeInTheDocument();
  });

  it('图表支持手动切换、全屏、PNG，并可切回表格导出 CSV', async () => {
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    const originalFullscreen = HTMLElement.prototype.requestFullscreen;
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
      configurable: true,
      value: requestFullscreen,
    });
    stubFetch(() =>
      sseResponse([
        { type: 'context', request_id: 'req-chart' },
        {
          type: 'result',
          data: [
            { 渠道: '自然', 播放量: 10 },
            { 渠道: '广告', 播放量: 8 },
          ],
          columns: ['渠道', '播放量'],
          row_count: 2,
          truncated: false,
        },
        {
          type: 'visualization',
          chart_spec: {
            schema_version: 'chart-spec/v1',
            type: 'bar',
            title: '渠道播放量',
            dimension: '渠道',
            metrics: ['播放量'],
            series: null,
            source: 'deterministic',
          },
        },
        {
          type: 'answer',
          summary: '已返回渠道播放量。',
          row_count: 2,
          columns: ['渠道', '播放量'],
          metrics: ['播放量'],
          time_range: '未限定',
          sql: 'SELECT channel, COUNT(*) FROM play_session GROUP BY channel',
        },
        { type: 'done', status: 'completed', duration_ms: 200, error: null },
      ]),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '按渠道统计播放量');

    expect(await screen.findByText('渠道播放量')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出 PNG' })).toBeInTheDocument();
    await user.click(screen.getByText('饼图'));
    expect(screen.getByText('播放量按渠道占比')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '全屏' }));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    await user.click(screen.getByText('表格'));
    expect(screen.getByRole('button', { name: '导出 CSV' })).toBeInTheDocument();

    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
      configurable: true,
      value: originalFullscreen,
    });
  });

  it('无数据结果显示空状态提示', async () => {
    stubFetch(() =>
      sseResponse([
        { type: 'context', request_id: 'req-empty' },
        {
          type: 'result',
          data: [],
          columns: [],
          row_count: 0,
          truncated: false,
        },
        { type: 'done', status: 'completed', duration_ms: 120, error: null },
      ]),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '没有数据的查询');
    expect(await screen.findByText('查询未返回数据。')).toBeInTheDocument();
  });

  it('低置信度追问展示澄清提示且不伪装成查询失败', async () => {
    stubFetch(() =>
      sseResponse([
        {
          type: 'context',
          request_id: 'req-clarification',
          conversation_id: 'conversation-test',
          standalone_question: '还有呢',
          context_resolution_confidence: 'low',
        },
        {
          type: 'clarification',
          request_id: 'req-clarification',
          message: '请补充指标、筛选、时间或粒度。',
        },
        { type: 'done', status: 'needs_input', duration_ms: 3, error: null },
      ]),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '还有呢');

    expect(await screen.findByText('需要补充条件')).toBeInTheDocument();
    expect(screen.getByText('请补充指标、筛选、时间或粒度。')).toBeInTheDocument();
    expect(screen.queryByText('查询失败')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /打开历史：还有呢（待补充）/ })).toBeInTheDocument();
  });

  it('追问建议只展示不自动执行，点击后才提交下一轮', async () => {
    const fetchMock = stubFetch(() =>
      sseResponse([
        {
          type: 'context',
          request_id: 'req-recommendations',
          conversation_id: 'conversation-test',
        },
        {
          type: 'answer',
          summary: '最近7天播放次数整体上升。',
          row_count: 1,
          columns: ['播放次数'],
          metrics: ['播放次数'],
          time_range: '最近7天',
          sql: 'SELECT 1',
        },
        {
          type: 'recommendations',
          questions: ['把时间范围改为上个月', '与上一周期对比同一指标', '按周查看同一指标趋势'],
          source: 'manual',
          llm_calls: 0,
        },
        { type: 'done', status: 'completed', duration_ms: 10, error: null },
      ]),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '最近7天播放趋势');

    expect(await screen.findByText('继续追问')).toBeInTheDocument();
    const queryCallsBefore = fetchMock.mock.calls.filter(([url]) => url === '/api/query');
    expect(queryCallsBefore).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '把时间范围改为上个月' }));
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.filter(([url]) => url === '/api/query')).toHaveLength(2);
    });
    const secondCall = fetchMock.mock.calls.filter(([url]) => url === '/api/query')[1];
    expect(JSON.parse(String(secondCall[1]?.body))).toEqual({
      query: '把时间范围改为上个月',
      conversation_id: 'conversation-test',
      parent_trace_id: 'req-recommendations',
    });
  });

  it('截断结果明确提示最多返回 500 行', async () => {
    stubFetch(() =>
      sseResponse([
        { type: 'context', request_id: 'req-truncated' },
        {
          type: 'result',
          data: [
            { name: 'A', category: 'x' },
            { name: 'B', category: 'y' },
          ],
          columns: ['name', 'category'],
          row_count: 500,
          truncated: true,
        },
        { type: 'done', status: 'completed', duration_ms: 900, error: null },
      ]),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '截断场景');
    expect(await screen.findByText('结果已截断')).toBeInTheDocument();
    expect(screen.getAllByText(/最多返回 500 行/).length).toBeGreaterThanOrEqual(1);
  });

  it('服务端错误流显示失败通知，可重试成功', async () => {
    let attempt = 0;
    stubFetch(() => {
      attempt += 1;
      if (attempt === 1) {
        return sseResponse([
          { type: 'context', request_id: 'req-err' },
          { type: 'error', stage: 'execution', message: 'SQL 执行超时' },
          { type: 'done', status: 'failed', duration_ms: 200, error: 'SQL 执行超时' },
        ]);
      }
      return sseResponse(TABLE_FLOW);
    });
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '会失败的查询');
    expect(await screen.findByText('查询失败')).toBeInTheDocument();
    expect(screen.getByText('SQL 执行超时')).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: '重试' })[0]);
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
  });

  it('网络连接失败提示后可重试', async () => {
    let attempt = 0;
    stubFetch(() => {
      attempt += 1;
      if (attempt === 1) return Promise.reject(new TypeError('Failed to fetch'));
      return sseResponse(TABLE_FLOW);
    });
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '网络抖动查询');
    expect(await screen.findByText('无法连接到查询服务，请检查网络后重试。')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: '重试' })[0]);
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
  });

  it('取消查询后不再接收结果', async () => {
    stubFetch(
      () =>
        new Promise<Response>((_resolve, reject) => {
          // 永不返回，直到客户端 abort。
          setTimeout(
            () => reject(new DOMException('The operation was aborted.', 'AbortError')),
            50,
          );
        }),
    );
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await typeAndRun(user, '一个慢查询');
    const cancelButton = await screen.findByRole('button', { name: '取消查询' });
    await user.click(cancelButton);
    expect(await screen.findByText('查询已取消')).toBeInTheDocument();
    expect(screen.getByText('未接收任何结果数据。')).toBeInTheDocument();
  });

  it('点击示例问题直接执行查询', async () => {
    const fetchMock = stubFetch(() => sseResponse(TABLE_FLOW));
    const user = userEvent.setup();
    render(<App auth={AUTH} />);
    await user.click(screen.getByRole('button', { name: '内容' }));
    await user.click(screen.getByRole('button', { name: '平台一共有多少个有声专辑' }));
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/query',
      expect.objectContaining({
        body: JSON.stringify({
          query: '平台一共有多少个有声专辑',
          conversation_id: 'conversation-test',
        }),
      }),
    );
  });

  it('保存洞察卡片，并通过当前权限重新打开后删除', async () => {
    const fetchMock = stubFetch(() => sseResponse(TABLE_FLOW));
    const user = userEvent.setup();
    render(<App auth={AUTH} />);

    await typeAndRun(user, '平台一共有多少个有声专辑');
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '保存洞察卡片' }));
    expect(await screen.findByText('洞察卡片已保存')).toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: '重新鉴权打开' }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith('/api/insight-cards/card-test/execute'),
        ),
      ).toBe(true);
    });
    const reopenCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/api/insight-cards/card-test/execute'),
    );
    expect(JSON.parse(String(reopenCall?.[1]?.body))).toEqual({
      conversation_id: 'conversation-test',
      parent_trace_id: 'req-table',
    });

    await user.click(screen.getByRole('button', { name: '删除洞察：平台一共有多少个有声专辑' }));
    expect(screen.queryByRole('button', { name: '重新鉴权打开' })).not.toBeInTheDocument();
  });

  it('恢复历史会话不重提旧问题，后续查询沿选中 Trace 分支继续', async () => {
    const fetchMock = stubRestoredConversation();
    const user = userEvent.setup();
    render(<App auth={AUTH} />);

    expect(await screen.findByText('已恢复历史轮次')).toBeInTheDocument();
    expect(screen.getByText('历史摘要：播放次数整体上升。')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/query')).toBe(false);

    await user.click(screen.getByRole('button', { name: /打开历史：最近7天播放趋势（已完成）/ }));
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/query')).toBe(false);

    const input = screen.getByRole('textbox', { name: '自然语言问题' });
    await user.clear(input);
    await user.type(input, '那上个月呢');
    await user.click(screen.getByRole('button', { name: '执行查询' }));
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
    const queryCall = fetchMock.mock.calls.find(([url]) => String(url) === '/api/query');
    expect(JSON.parse(String(queryCall?.[1]?.body))).toEqual({
      query: '那上个月呢',
      conversation_id: 'conversation-restored',
      parent_trace_id: 'trace-restored',
    });
  });

  it('支持搜索、重命名、归档和分支重生成', async () => {
    const fetchMock = stubRestoredConversation();
    const user = userEvent.setup();
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('播放趋势（已重命名）');
    render(<App auth={AUTH} />);
    expect(await screen.findByText('播放趋势会话')).toBeInTheDocument();

    const search = screen.getByRole('searchbox', { name: '搜索会话' });
    await user.type(search, '订单');
    expect(screen.getByText('订单分析会话')).toBeInTheDocument();
    expect(screen.queryByText('播放趋势会话')).not.toBeInTheDocument();
    await user.clear(search);

    await user.click(screen.getByRole('button', { name: '重命名会话：播放趋势会话' }));
    expect(await screen.findByText('播放趋势（已重命名）')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '归档会话：订单分析会话' }));
    expect(screen.queryByText('订单分析会话')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '重生成：最近7天播放趋势' }));
    expect(await screen.findByText('已执行查询，共返回 2 行。')).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url) === '/api/traces/trace-restored/regenerate'),
    ).toBe(true);
    prompt.mockRestore();
  });
});
