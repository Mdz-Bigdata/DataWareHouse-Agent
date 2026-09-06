import { apiFetch } from './auth';

export type FeedbackVerdict = 'correct' | 'incorrect';

export interface TraceFeedbackInput {
  verdict: FeedbackVerdict;
  reasons: string[];
  comment: string;
}

export interface TraceFeedbackResult extends TraceFeedbackInput {
  id: string;
  trace_id: string;
  template_signature: string;
  candidate_revision_id: string | null;
  positive_count: number | null;
}

export class FeedbackRequestError extends Error {}

export async function submitTraceFeedback(
  traceId: string,
  input: TraceFeedbackInput,
): Promise<TraceFeedbackResult> {
  let response: Response;
  try {
    response = await apiFetch(`/api/traces/${encodeURIComponent(traceId)}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  } catch {
    throw new FeedbackRequestError('无法连接反馈服务，请稍后重试。');
  }
  if (!response.ok) {
    let detail = `反馈提交失败（HTTP ${response.status}）。`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === 'string' && payload.detail) detail = payload.detail;
    } catch {
      // Keep the status-based message for a non-JSON response.
    }
    throw new FeedbackRequestError(detail);
  }
  return (await response.json()) as TraceFeedbackResult;
}
