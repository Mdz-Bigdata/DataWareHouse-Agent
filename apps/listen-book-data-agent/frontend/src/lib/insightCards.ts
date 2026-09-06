import { apiFetch } from './auth';

export interface InsightCard {
  id: string;
  question: string;
  answer_summary: string;
  sql_template: string;
  parameter_types: string[];
  chart_spec: Record<string, unknown>;
  version_info: Record<string, unknown>;
  created_at: string;
}

async function requireOk(response: Response, action: string): Promise<void> {
  if (response.ok) return;
  let detail = '';
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') detail = `：${body.detail}`;
  } catch {
    // An HTTP status is still enough to provide a useful failure message.
  }
  throw new Error(`${action}失败（HTTP ${response.status}）${detail}`);
}

export async function fetchInsightCards(): Promise<InsightCard[]> {
  const response = await apiFetch('/api/insight-cards');
  await requireOk(response, '加载洞察卡片');
  return (await response.json()) as InsightCard[];
}

export async function saveInsightCard(traceId: string): Promise<InsightCard> {
  const response = await apiFetch(`/api/insight-cards/from-trace/${encodeURIComponent(traceId)}`, {
    method: 'POST',
  });
  await requireOk(response, '保存洞察卡片');
  return (await response.json()) as InsightCard;
}

export async function deleteInsightCard(cardId: string): Promise<void> {
  const response = await apiFetch(`/api/insight-cards/${encodeURIComponent(cardId)}`, {
    method: 'DELETE',
  });
  await requireOk(response, '删除洞察卡片');
}
