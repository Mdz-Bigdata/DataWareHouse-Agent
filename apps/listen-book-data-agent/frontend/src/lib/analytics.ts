import { apiFetch } from './auth';

const BASE = '/api/admin/analytics';

export interface DailyStat {
  date: string;
  total: number;
  completed: number;
  failed: number;
  success_rate: number;
}

export interface AnalyticsOverview {
  total: number;
  completed: number;
  failed: number;
  success_rate: number;
  avg_duration_ms: number | null;
  daily_stats: DailyStat[];
}

export interface FailureReason {
  reason: string;
  count: number;
}

export interface DurationBucket {
  bucket: string;
  count: number;
  completed: number;
  avg_ms: number;
}

export interface PhaseStat {
  step: string;
  avg_ms: number;
  success_count: number;
  error_count: number;
}

export interface AnalyticsStats {
  overview: AnalyticsOverview;
  failure_reasons: FailureReason[];
  duration_buckets: DurationBucket[];
  phase_stats: PhaseStat[];
}

export interface TraceDetail {
  id: string;
  user_id: string | null;
  username: string | null;
  query_text: string;
  status: string;
  sql: string | null;
  error_message: string | null;
  total_duration_ms: number | null;
  build_id: string | null;
  started_at: string;
  completed_at: string | null;
  phases?: TracePhase[];
}

export interface TracePhase {
  sequence: number;
  step: string;
  status: string;
  duration_ms: number;
  sql: string | null;
  error_message: string | null;
}

export async function fetchAnalyticsStats(days = 7): Promise<AnalyticsStats> {
  const resp = await apiFetch(`${BASE}/stats?days=${days}`);
  if (!resp.ok) throw new Error('获取查询统计失败');
  return resp.json();
}

export async function fetchAllTraces(
  limit = 100,
  status?: 'completed' | 'failed',
): Promise<TraceDetail[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set('status', status);
  const resp = await apiFetch(`${BASE}/traces?${params}`);
  if (!resp.ok) throw new Error('获取查询明细失败');
  return resp.json();
}
