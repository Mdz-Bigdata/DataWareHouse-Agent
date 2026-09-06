import { apiFetch } from './auth';

export interface DatasourceInfo {
  key: 'meta' | 'warehouse';
  label: string;
  host: string;
  port: number;
  database: string;
  user: string;
}

export interface SemanticOverview {
  active_build_id: string | null;
  build_created_at: string | null;
  tables: number;
  columns: number;
  metrics: number;
  relationships: number;
  datasources: DatasourceInfo[];
}

export interface SemanticTable {
  id: string;
  name: string;
  role: string;
  description: string;
  alias: string[];
  domain: string;
}

export interface SemanticTableUpdate {
  name?: string;
  description?: string;
  alias?: string[];
  role?: string;
}

export interface SemanticColumn {
  id: string;
  table_id: string;
  name: string;
  type: string;
  role: string;
  description: string;
  alias: string[];
  examples: unknown[];
  nullable: boolean;
  sensitive: boolean;
  sync: boolean;
  enum_values: unknown[];
}

export interface SemanticColumnUpdate {
  description?: string;
  alias?: string[];
  examples?: unknown[];
  role?: string;
  sensitive?: boolean;
  sync?: boolean;
  enum_values?: unknown[];
}

export interface SemanticMetric {
  id: string;
  name: string;
  description: string;
  alias: string[];
  formula: string;
  relevant_columns: string[];
  filters: string[];
  time_column: string | null;
  unit: string;
  dimensions: string[];
  snapshot: boolean;
}

export interface SemanticMetricUpsert {
  name: string;
  description: string;
  alias: string[];
  formula: string;
  relevant_columns: string[];
  filters: string[];
  time_column: string | null;
  unit: string;
  dimensions: string[];
  snapshot: boolean;
}

export type SemanticMetricCreate = SemanticMetricUpsert & { id: string };

export interface SemanticRelationship {
  id: string;
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  relationship_type: string;
  condition: string | null;
  physical: boolean;
}

export interface SemanticRelationshipUpsert {
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  relationship_type: string;
  condition: string | null;
  physical: boolean;
}

export type SemanticRelationshipCreate = SemanticRelationshipUpsert & { id?: string };

export interface DatasourceTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

const BASE = '/api/admin/semantic';

async function parseError(response: Response, fallback: string): Promise<Error> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === 'string' && data.detail) return new Error(data.detail);
  } catch {
    // 非 JSON 响应
  }
  return new Error(`${fallback}（HTTP ${response.status}）。`);
}

async function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${BASE}${path}`, init);
  if (!response.ok) throw await parseError(response, fallback);
  return (await response.json()) as T;
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const fetchSemanticOverview = () =>
  request<SemanticOverview>('/overview', '加载语义层总览失败');

export const testDatasource = (target: 'meta' | 'warehouse') =>
  request<DatasourceTestResult>('/datasources/test', '数据源测试失败', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target }),
  });

export const listSemanticTables = () => request<SemanticTable[]>('/tables', '加载数据表失败');

export const updateSemanticTable = (id: string, body: SemanticTableUpdate) =>
  request<SemanticTable>(`/tables/${encodeURIComponent(id)}`, '保存数据表失败', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const listSemanticColumns = (tableId: string) =>
  request<SemanticColumn[]>(`/tables/${encodeURIComponent(tableId)}/columns`, '加载字段失败');

export const updateSemanticColumn = (id: string, body: SemanticColumnUpdate) =>
  request<SemanticColumn>(`/columns/${encodeURIComponent(id)}`, '保存字段失败', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const listSemanticMetrics = () => request<SemanticMetric[]>('/metrics', '加载指标失败');

export const createSemanticMetric = (body: SemanticMetricCreate) =>
  request<SemanticMetric>('/metrics', '新增指标失败', jsonInit('POST', body));

export const updateSemanticMetric = (id: string, body: SemanticMetricUpsert) =>
  request<SemanticMetric>(`/metrics/${encodeURIComponent(id)}`, '保存指标失败', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const deleteSemanticMetric = (id: string) =>
  request<{ status: string }>(`/metrics/${encodeURIComponent(id)}`, '删除指标失败', {
    method: 'DELETE',
  });

export const listSemanticRelationships = () =>
  request<SemanticRelationship[]>('/relationships', '加载关联关系失败');

export const createSemanticRelationship = (body: SemanticRelationshipCreate) =>
  request<SemanticRelationship>('/relationships', '新增关联关系失败', jsonInit('POST', body));

export const updateSemanticRelationship = (id: string, body: SemanticRelationshipUpsert) =>
  request<SemanticRelationship>(`/relationships/${encodeURIComponent(id)}`, '保存关联关系失败', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const deleteSemanticRelationship = (id: string) =>
  request<{ status: string }>(`/relationships/${encodeURIComponent(id)}`, '删除关联关系失败', {
    method: 'DELETE',
  });

// ==================== M3c：召回测试与知识库重建 ====================

export interface RecallTestColumn {
  id: string;
  table_id: string;
  name: string;
  description: string;
  alias: string[];
  role: string;
}

export interface RecallTestMetric {
  id: string;
  name: string;
  description: string;
  alias: string[];
  formula: string;
}

export interface RecallTestResult {
  question: string;
  keywords: string[];
  terms: string[];
  tables: string[];
  columns: RecallTestColumn[];
  metrics: RecallTestMetric[];
  warnings: string[];
}

export interface RebuildStatus {
  status: 'idle' | 'running' | 'completed' | 'failed';
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface SemanticRelease {
  id: string;
  version: number;
  version_label: string;
  domain: string;
  datasource: string;
  release_kind: 'activation' | 'rollback';
  knowledge_build_id: string;
  query_set_id: string;
  query_set_version: number | null;
  business_rule_set_id: string;
  business_rule_set_version: number | null;
  source_release_id: string | null;
  created_by: string;
  created_at: string;
  active: boolean;
}

export const recallTest = (question: string) =>
  request<RecallTestResult>('/recall-test', '召回测试失败', jsonInit('POST', { question }));

export const startRebuild = () =>
  request<{ status: string }>('/rebuild', '启动重建失败', { method: 'POST' });

export const fetchRebuildStatus = () =>
  request<RebuildStatus>('/rebuild/status', '获取重建状态失败');

export const listSemanticReleases = () =>
  request<SemanticRelease[]>('/releases', '加载语义发布记录失败');

export const rollbackSemanticRelease = (releaseId: string) =>
  request<SemanticRelease>(
    `/releases/${encodeURIComponent(releaseId)}/rollback`,
    '回滚语义发布失败',
    { method: 'POST' },
  );
