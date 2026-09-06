import { readSseFrames } from './sse';
import { apiFetch } from './auth';
import { parseQueryEvent, type QueryEvent } from '../types/events';

/** Thrown for transport-level failures (DNS/TCP/HTTP status), not data errors. */
export class QueryRequestError extends Error {}

export interface RunQueryOptions {
  query: string;
  conversationId?: string | null;
  parentTraceId?: string | null;
  regenerateTraceId?: string | null;
  insightCardId?: string | null;
  signal: AbortSignal;
  onEvent: (event: QueryEvent) => void;
  /** Called once the stream ends normally; `sawDone` is false if the server
   * closed the connection without sending the final `done` event. */
  onClose?: (sawDone: boolean) => void;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

/**
 * POST /api/query and stream normalized events to the caller.
 *
 * Aborts propagate as AbortError (DOMException); HTTP and connection failures
 * surface as QueryRequestError. A stream that ends without `done` is reported
 * through onClose(false) so callers can mark the result as incomplete.
 */
export async function runQuery(options: RunQueryOptions): Promise<void> {
  const {
    query,
    conversationId,
    parentTraceId,
    regenerateTraceId,
    insightCardId,
    signal,
    onEvent,
    onClose,
  } = options;
  const endpoint = regenerateTraceId
    ? `/api/traces/${encodeURIComponent(regenerateTraceId)}/regenerate`
    : insightCardId
      ? `/api/insight-cards/${encodeURIComponent(insightCardId)}/execute`
      : '/api/query';
  const body = regenerateTraceId
    ? {}
    : insightCardId
      ? {
          ...(conversationId ? { conversation_id: conversationId } : {}),
          ...(parentTraceId ? { parent_trace_id: parentTraceId } : {}),
        }
      : {
          query,
          ...(conversationId ? { conversation_id: conversationId } : {}),
          ...(parentTraceId ? { parent_trace_id: parentTraceId } : {}),
        };
  let response: Response;
  try {
    response = await apiFetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new QueryRequestError('无法连接到查询服务，请检查网络后重试。');
  }
  if (!response.ok || !response.body) {
    if (response.status === 401) {
      throw new QueryRequestError('登录已过期，请重新登录。');
    }
    throw new QueryRequestError(`查询请求失败（HTTP ${response.status}）。`);
  }

  let sawDone = false;
  for await (const frame of readSseFrames(response.body)) {
    const event = parseQueryEvent(frame.data);
    if (!event) continue;
    if (event.type === 'done') sawDone = true;
    onEvent(event);
    if (signal.aborted) return;
  }
  onClose?.(sawDone);
}
