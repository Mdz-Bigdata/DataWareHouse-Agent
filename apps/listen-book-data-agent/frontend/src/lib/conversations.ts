import type { AnalysisPlan, QueryPlanV1 } from '../types/events';
import type { HistoryEntry } from '../state/historyReducer';
import { apiFetch } from './auth';

export interface Conversation {
  id: string;
  title: string;
  status: 'active' | 'archived';
  created_at: string;
  updated_at: string;
}

interface ConversationTurn {
  id: string;
  query_text: string;
  standalone_question: string | null;
  status: string;
  total_duration_ms: number | null;
  started_at: string;
  completed_at: string | null;
  parent_trace_id: string | null;
  regenerate_of_trace_id: string | null;
  query_plan_summary: Partial<AnalysisPlan> | QueryPlanV1 | null;
  answer_summary: string | null;
  chart_spec: Record<string, unknown> | null;
  sql: string | null;
  build_id: string | null;
  semantic_release_id: string | null;
  semantic_release_version: number | null;
  query_set_id: string | null;
  query_set_version: number | null;
  business_rule_set_id: string | null;
  business_rule_set_version: number | null;
  policy_version: string | null;
  policy_hash: string | null;
}

function requireOk(response: Response, action: string): void {
  if (!response.ok) throw new Error(`${action}失败（HTTP ${response.status}）`);
}

export async function fetchConversations(search = ''): Promise<Conversation[]> {
  const params = search.trim() ? `?search=${encodeURIComponent(search.trim())}` : '';
  const response = await apiFetch(`/api/conversations${params}`);
  requireOk(response, '加载会话');
  return (await response.json()) as Conversation[];
}

export async function createConversation(title: string): Promise<Conversation> {
  const response = await apiFetch('/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title.trim() || '新分析' }),
  });
  requireOk(response, '创建会话');
  return (await response.json()) as Conversation;
}

export async function updateConversation(
  conversationId: string,
  patch: { title?: string; status?: 'active' | 'archived' },
): Promise<Conversation> {
  const response = await apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  requireOk(response, '更新会话');
  return (await response.json()) as Conversation;
}

export async function fetchConversationTurns(conversationId: string): Promise<HistoryEntry[]> {
  const response = await apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}/turns`);
  requireOk(response, '加载会话轮次');
  const turns = (await response.json()) as ConversationTurn[];
  return turns
    .map((turn, index) => ({
      id: -(index + 1),
      requestId: turn.id,
      conversationId,
      parentTraceId: turn.parent_trace_id,
      regenerateOfTraceId: turn.regenerate_of_trace_id,
      question: turn.query_text,
      standaloneQuestion: turn.standalone_question,
      status: mapStatus(turn.status),
      rowCount: null,
      durationMs: turn.total_duration_ms,
      startedAt: Date.parse(turn.started_at) || Date.now(),
      analysisPlan: normalizePlan(turn.query_plan_summary),
      queryPlan: normalizeQueryPlan(turn.query_plan_summary),
      answerSummary: turn.answer_summary,
      chartSpec: turn.chart_spec,
      sql: turn.sql,
      buildId: turn.build_id,
      semanticReleaseId: turn.semantic_release_id,
      semanticReleaseVersion: turn.semantic_release_version,
      querySetId: turn.query_set_id,
      querySetVersion: turn.query_set_version,
      businessRuleSetId: turn.business_rule_set_id,
      businessRuleSetVersion: turn.business_rule_set_version,
      policyVersion: turn.policy_version,
      policyHash: turn.policy_hash,
    }))
    .reverse();
}

function mapStatus(status: string): HistoryEntry['status'] {
  if (status === 'completed' || status === 'failed' || status === 'needs_input') return status;
  return 'cancelled';
}

function normalizePlan(plan: Partial<AnalysisPlan> | QueryPlanV1 | null): AnalysisPlan | null {
  if (!plan?.intent || isQueryPlan(plan)) return null;
  return {
    intent: plan.intent,
    metric_hints: plan.metric_hints ?? [],
    dimensions: plan.dimensions ?? [],
    filters: plan.filters ?? [],
    time_range: plan.time_range ?? { start: null, end: null, label: null },
    time_grain: plan.time_grain ?? null,
    top_n: plan.top_n ?? null,
    sort_direction: plan.sort_direction ?? null,
    comparison: plan.comparison ?? null,
  };
}

function normalizeQueryPlan(plan: Partial<AnalysisPlan> | QueryPlanV1 | null): QueryPlanV1 | null {
  return isQueryPlan(plan) ? plan : null;
}

function isQueryPlan(plan: Partial<AnalysisPlan> | QueryPlanV1 | null): plan is QueryPlanV1 {
  return Boolean(plan && 'schema_version' in plan && plan.schema_version === 'query-plan/v1');
}
