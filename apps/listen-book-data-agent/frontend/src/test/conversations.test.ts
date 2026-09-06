import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createConversation,
  fetchConversations,
  fetchConversationTurns,
  updateConversation,
} from '../lib/conversations';

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('conversation client', () => {
  it('creates, searches and updates owner-scoped conversations', async () => {
    const conversation = {
      id: 'conversation-1',
      title: '播放趋势',
      status: 'active',
      created_at: '2026-07-19T10:00:00',
      updated_at: '2026-07-19T10:00:00',
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(conversation, 201))
      .mockResolvedValueOnce(jsonResponse([conversation]))
      .mockResolvedValueOnce(jsonResponse({ ...conversation, title: '渠道趋势' }));
    vi.stubGlobal('fetch', fetchMock);

    await createConversation('播放趋势');
    await fetchConversations('播放');
    const updated = await updateConversation('conversation-1', { title: '渠道趋势' });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/conversations');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/conversations?search=%E6%92%AD%E6%94%BE');
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({ title: '渠道趋势' });
    expect(updated.title).toBe('渠道趋势');
  });

  it('maps persisted turns newest-first without inventing result rows', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse([
          {
            id: 'trace-1',
            query_text: '播放趋势',
            standalone_question: '最近7天按天播放次数趋势',
            status: 'completed',
            total_duration_ms: 120,
            started_at: '2026-07-19T10:00:00',
            completed_at: '2026-07-19T10:00:01',
            parent_trace_id: null,
            regenerate_of_trace_id: null,
            query_plan_summary: { intent: 'trend', metric_hints: ['播放次数'] },
            answer_summary: '整体上升。',
            chart_spec: { type: 'line' },
            sql: 'SELECT 1',
            build_id: 'build-1',
            policy_version: 'policy-v1',
            policy_hash: 'hash-1',
          },
          {
            id: 'trace-2',
            query_text: '那上个月呢',
            standalone_question: '上月按天播放次数趋势',
            status: 'needs_input',
            total_duration_ms: 3,
            started_at: '2026-07-19T10:01:00',
            completed_at: '2026-07-19T10:01:01',
            parent_trace_id: 'trace-1',
            regenerate_of_trace_id: null,
            query_plan_summary: null,
            answer_summary: null,
            chart_spec: null,
            sql: null,
            build_id: null,
            policy_version: 'policy-v2',
            policy_hash: 'hash-2',
          },
        ]),
      ),
    );

    const turns = await fetchConversationTurns('conversation-1');

    expect(turns.map((turn) => turn.requestId)).toEqual(['trace-2', 'trace-1']);
    expect(turns[0].status).toBe('needs_input');
    expect(turns[1].analysisPlan?.time_range).toEqual({
      start: null,
      end: null,
      label: null,
    });
    expect('rows' in turns[1]).toBe(false);
  });
});
