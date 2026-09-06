import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchTraces } from '../lib/traces';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchTraces', () => {
  it('保留失败状态，并把取消或遗留 running 记录映射为已取消', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify([
            {
              id: 'failed-1',
              query_text: '失败查询',
              status: 'failed',
              total_duration_ms: 12,
              started_at: '2026-07-18T07:00:00',
              completed_at: '2026-07-18T07:00:01',
            },
            {
              id: 'cancelled-1',
              query_text: '取消查询',
              status: 'cancelled',
              total_duration_ms: 20,
              started_at: '2026-07-18T07:01:00',
              completed_at: '2026-07-18T07:01:01',
            },
            {
              id: 'running-1',
              query_text: '遗留查询',
              status: 'running',
              total_duration_ms: null,
              started_at: '2026-07-18T07:02:00',
              completed_at: null,
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );

    const entries = await fetchTraces();

    expect(entries.map((entry) => entry.status)).toEqual(['failed', 'cancelled', 'cancelled']);
  });
});
