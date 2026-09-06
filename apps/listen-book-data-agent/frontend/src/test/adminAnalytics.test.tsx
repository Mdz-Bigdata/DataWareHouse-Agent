import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchAllTraces,
  fetchAnalyticsStats,
  type AnalyticsStats,
  type TraceDetail,
} from '../lib/analytics';
import { copyText } from '../lib/clipboard';
import { AdminAnalyticsPage } from '../pages/AdminAnalyticsPage';

vi.mock('../lib/analytics', () => ({
  fetchAnalyticsStats: vi.fn(),
  fetchAllTraces: vi.fn(),
}));

vi.mock('../lib/clipboard', () => ({
  copyText: vi.fn().mockResolvedValue(true),
}));

const EMPTY_STATS: AnalyticsStats = {
  overview: {
    total: 1,
    completed: 0,
    failed: 0,
    success_rate: 0,
    avg_duration_ms: null,
    daily_stats: [],
  },
  failure_reasons: [],
  duration_buckets: [],
  phase_stats: [],
};

function trace(overrides: Partial<TraceDetail>): TraceDetail {
  return {
    id: 'trace-1',
    user_id: 'user-1',
    username: 'admin',
    query_text: '测试查询',
    status: 'running',
    sql: null,
    error_message: null,
    total_duration_ms: null,
    build_id: null,
    started_at: '2026-07-18T07:21:13',
    completed_at: null,
    ...overrides,
  };
}

describe('AdminAnalyticsPage', () => {
  beforeEach(() => {
    vi.mocked(fetchAnalyticsStats).mockResolvedValue(EMPTY_STATS);
    vi.mocked(fetchAllTraces).mockResolvedValue([]);
  });

  it('不会把未完成的 running 记录误报为失败，并说明没有失败原因', async () => {
    vi.mocked(fetchAllTraces).mockResolvedValue([
      trace({ query_text: '连接中断的查询', status: 'running' }),
    ]);
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AdminAnalyticsPage />
      </MemoryRouter>,
    );

    const question = await screen.findByText('连接中断的查询');
    const row = question.closest('tr');
    expect(row).not.toBeNull();
    expect(within(row!).getByText('进行中')).toBeInTheDocument();
    expect(screen.getByText('暂无失败原因')).toBeInTheDocument();
    expect(screen.getByText(/未完成\/取消 1/)).toBeInTheDocument();

    await user.click(row!);
    expect(screen.getByText(/未正常写入终态/)).toBeInTheDocument();
  });

  it('展开真正的失败记录时展示后端保存的失败原因', async () => {
    vi.mocked(fetchAnalyticsStats).mockResolvedValue({
      ...EMPTY_STATS,
      overview: { ...EMPTY_STATS.overview, failed: 1 },
      failure_reasons: [{ reason: 'SQL 字段未授权', count: 1 }],
    });
    vi.mocked(fetchAllTraces).mockResolvedValue([
      trace({
        query_text: '这是一个需要完整展示并支持复制的失败查询问题',
        status: 'failed',
        sql: 'WITH bad_data AS (SELECT missing_column FROM play_session) SELECT * FROM bad_data',
        error_message: 'SQL 字段未授权',
        total_duration_ms: 123,
        phases: [
          {
            sequence: 1,
            step: '校验SQL',
            status: 'error',
            duration_ms: 4,
            sql: 'SELECT missing_column FROM play_session',
            error_message: '字段未授权：play_session.missing_column',
          },
        ],
      }),
    ]);
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AdminAnalyticsPage />
      </MemoryRouter>,
    );

    const question = await screen.findByText('这是一个需要完整展示并支持复制的失败查询问题');
    const row = question.closest('tr');
    expect(row).not.toBeNull();
    expect(within(row!).getByText('失败')).toBeInTheDocument();

    await user.click(row!);
    expect(screen.getByText('完整问题')).toBeInTheDocument();
    expect(screen.getByText('失败 SQL')).toBeInTheDocument();
    expect(
      screen.getByText(
        'WITH bad_data AS (SELECT missing_column FROM play_session) SELECT * FROM bad_data',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText('失败原因')).toHaveLength(2);
    expect(screen.getAllByText('SQL 字段未授权').length).toBeGreaterThan(0);
    expect(screen.getByText('SQL 校验记录')).toBeInTheDocument();
    expect(screen.getByText(/第 1 次校验 · 失败/)).toBeInTheDocument();
    expect(screen.getByText('SELECT missing_column FROM play_session')).toBeInTheDocument();
    expect(screen.getByText('字段未授权：play_session.missing_column')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '复制完整问题' }));
    expect(copyText).toHaveBeenCalledWith('这是一个需要完整展示并支持复制的失败查询问题');
  });

  it('旧失败记录没有 SQL 时给出明确说明', async () => {
    vi.mocked(fetchAllTraces).mockResolvedValue([
      trace({ query_text: '旧失败记录', status: 'failed', error_message: '校验失败', sql: null }),
    ]);
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <AdminAnalyticsPage />
      </MemoryRouter>,
    );

    const question = await screen.findByText('旧失败记录');
    await user.click(question.closest('tr')!);

    expect(screen.getByText(/该历史记录未保存失败 SQL/)).toBeInTheDocument();
  });
});
