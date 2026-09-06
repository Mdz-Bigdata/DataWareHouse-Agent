import { apiFetch } from './auth';
import type { HistoryEntry } from '../state/historyReducer';

interface TraceItem {
  id: string;
  query_text: string;
  status: string;
  total_duration_ms: number | null;
  started_at: string;
  completed_at: string | null;
}

/**
 * 拉取当前登录用户的查询记录并映射为历史条目。
 * 后端已按属主过滤；历史条目使用负数 id，与会话内自增 id 区分（不会被打补丁）。
 */
export async function fetchTraces(): Promise<HistoryEntry[]> {
  const response = await apiFetch('/api/traces');
  if (!response.ok) return [];
  const traces = (await response.json()) as TraceItem[];
  return traces.map((trace, index) => ({
    id: -(index + 1),
    requestId: trace.id,
    conversationId: null,
    parentTraceId: null,
    regenerateOfTraceId: null,
    question: trace.query_text,
    standaloneQuestion: null,
    // 刷新后仍为 running 的记录属于未正常收尾的历史请求，按已取消展示，避免误报失败。
    status:
      trace.status === 'completed'
        ? 'completed'
        : trace.status === 'failed'
          ? 'failed'
          : 'cancelled',
    rowCount: null,
    durationMs: trace.total_duration_ms,
    startedAt: Date.parse(trace.started_at) || Date.now(),
    analysisPlan: null,
    queryPlan: null,
    answerSummary: null,
    chartSpec: null,
    sql: null,
    buildId: null,
    semanticReleaseId: null,
    semanticReleaseVersion: null,
    querySetId: null,
    querySetVersion: null,
    businessRuleSetId: null,
    businessRuleSetVersion: null,
    policyVersion: null,
    policyHash: null,
  }));
}

/** 清空当前用户的查询记录（服务端按属主删除）。 */
export async function clearTraces(): Promise<void> {
  await apiFetch('/api/traces', { method: 'DELETE' });
}
