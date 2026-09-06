import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryRequestError, runQuery } from '../lib/queryClient';
import type { QueryEvent } from '../types/events';

function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

const FLOW = [
  'event: context\ndata: {"type":"context","request_id":"req-9"}\n\n',
  'event: progress\ndata: {"type":"progress","step":"分析问题","status":"success"}\n\n',
  'event: done\ndata: {"type":"done","status":"completed","duration_ms":5,"error":null}\n\n',
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('runQuery', () => {
  it('posts the question and streams parsed events', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(FLOW));
    vi.stubGlobal('fetch', fetchMock);

    const events: QueryEvent[] = [];
    let closed: boolean | null = null;
    await runQuery({
      query: '本月播放量',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
      onClose: (sawDone) => {
        closed = sawDone;
      },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/query',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ query: '本月播放量' }),
      }),
    );
    expect(events.map((event) => event.type)).toEqual(['context', 'progress', 'done']);
    expect(closed).toBe(true);
  });

  it('reports a stream that ends without done via onClose(false)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([FLOW[0]])));
    let closed: boolean | null = null;
    await runQuery({
      query: 'q',
      signal: new AbortController().signal,
      onEvent: () => undefined,
      onClose: (sawDone) => {
        closed = sawDone;
      },
    });
    expect(closed).toBe(false);
  });

  it('sends conversation branches and uses the regenerate endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse(FLOW))
      .mockResolvedValueOnce(sseResponse(FLOW));
    vi.stubGlobal('fetch', fetchMock);

    await runQuery({
      query: '那上个月呢',
      conversationId: 'conversation-1',
      parentTraceId: 'trace-parent',
      signal: new AbortController().signal,
      onEvent: () => undefined,
    });
    await runQuery({
      query: '原问题',
      regenerateTraceId: 'trace-source',
      signal: new AbortController().signal,
      onEvent: () => undefined,
    });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/query');
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      query: '那上个月呢',
      conversation_id: 'conversation-1',
      parent_trace_id: 'trace-parent',
    });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/traces/trace-source/regenerate');
    expect(fetchMock.mock.calls[1][1]?.body).toBe('{}');
  });

  it('reopens an insight card through its reauthorization endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(FLOW));
    vi.stubGlobal('fetch', fetchMock);

    await runQuery({
      query: '卡片中的原问题',
      insightCardId: 'card/1',
      conversationId: 'conversation-1',
      parentTraceId: 'trace-parent',
      signal: new AbortController().signal,
      onEvent: () => undefined,
    });

    expect(fetchMock.mock.calls[0][0]).toBe('/api/insight-cards/card%2F1/execute');
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      conversation_id: 'conversation-1',
      parent_trace_id: 'trace-parent',
    });
  });

  it('skips malformed event payloads instead of crashing', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse([
            'event: progress\ndata: {not json}\n\n',
            'event: mystery\ndata: {"type":"mystery"}\n\n',
            FLOW[2],
          ]),
        ),
    );
    const events: QueryEvent[] = [];
    await runQuery({
      query: 'q',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });
    expect(events.map((event) => event.type)).toEqual(['done']);
  });

  it('throws QueryRequestError on HTTP failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 500 })));
    await expect(
      runQuery({ query: 'q', signal: new AbortController().signal, onEvent: () => undefined }),
    ).rejects.toBeInstanceOf(QueryRequestError);
  });

  it('throws QueryRequestError on connection failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(
      runQuery({ query: 'q', signal: new AbortController().signal, onEvent: () => undefined }),
    ).rejects.toThrow('无法连接到查询服务');
  });

  it('propagates abort errors for the caller to treat as cancellation', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () => {
              reject(new DOMException('The operation was aborted.', 'AbortError'));
            });
          }),
      ),
    );
    const promise = runQuery({
      query: 'q',
      signal: controller.signal,
      onEvent: () => undefined,
    });
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
  });
});
