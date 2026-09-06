/**
 * Discriminated union covering the backend SSE event protocol.
 * Mirrors `QueryService.events` in app/services/query_service.py.
 */

export type RowValue = string | number | boolean | null;
export type Row = Record<string, RowValue>;

export type ChartType = 'table' | 'kpi' | 'bar' | 'line' | 'pie';

export interface ChartSpecV1 {
  schema_version: 'chart-spec/v1';
  type: ChartType;
  title: string;
  dimension: string | null;
  metrics: string[];
  series: string | null;
  source: 'deterministic' | 'llm_validated';
}

export interface TimeRange {
  start: string | null;
  end: string | null;
  label: string | null;
}

export interface AnalysisPlan {
  intent: string;
  metric_hints: string[];
  dimensions: string[];
  filters: string[];
  time_range: TimeRange;
  time_grain: string | null;
  top_n: number | null;
  sort_direction: string | null;
  comparison: string | null;
}

export interface SemanticRefV1 {
  semantic_id: string;
  label: string;
}

export interface QueryPlanV1 {
  schema_version: 'query-plan/v1';
  intent: string;
  complexity: 'EASY' | 'NON_NESTED' | 'NESTED';
  metrics: SemanticRefV1[];
  dimensions: SemanticRefV1[];
  filters: Array<{
    filter_id: string;
    field_ids: string[];
    operator: string;
    values: string[];
    label: string;
    location: string;
    filter_only: boolean;
  }>;
  time: {
    field_id: string | null;
    start: string | null;
    end: string | null;
    label: string | null;
    grain: string | null;
  };
  sort: Array<{ semantic_id: string; direction: 'asc' | 'desc' }>;
  join_path: string[];
  subplans: Array<{
    subplan_id: string;
    purpose: string;
    metric_ids: string[];
    dimension_ids: string[];
    filter_ids: string[];
  }>;
  limit: number | null;
  comparison: string | null;
  source_hints: Record<string, unknown>;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  available: boolean;
}

export interface SemanticTermMatch {
  term_key: string;
  standard_term: string;
  version: number;
  bindings: Array<{ kind: string; semantic_id: string }>;
}

export interface VerifiedQueryMatch {
  case_key: string;
  revision_id?: string;
  question?: string;
  similarity?: number;
}

export interface BusinessRuleMatch {
  rule_key: string;
  version: number;
  rule_type: string;
}

export interface ProgressEvent {
  type: 'progress';
  step: string;
  status: 'running' | 'success' | 'error' | 'skipped' | 'degraded';
  request_id?: string;
  duration_ms?: number;
}

export interface ContextEvent {
  type: 'context';
  request_id: string;
  analysis_plan?: AnalysisPlan;
  query_plan?: QueryPlanV1;
  planning_roles?: Array<'Selector' | 'Decomposer' | 'Refiner'>;
  selected_semantics?: {
    metric_ids: string[];
    field_ids: string[];
    table_ids: string[];
    relationship_ids: string[];
  };
  decomposed_query?: Array<Record<string, unknown>>;
  query_plan_refined?: boolean;
  dry_plan_status?: 'validated';
  dry_plan_checks?: string[];
  sql_validation_stages?: Array<
    'ast_permissions' | 'rls_injection' | 'post_rls_ast' | 'explain_cost' | 'read_only_timeout'
  >;
  explain_estimate?: {
    estimated_cost: number | null;
    estimated_rows: number | null;
    source: string;
  } | null;
  explain_budget?: { max_cost: number; max_rows: number };
  execution_mode?: 'read_only';
  execution_timeout_seconds?: number;
  tables?: string[];
  warnings?: string[];
  build_id?: string;
  generation_mode?: 'legacy' | 'dsl';
  generation_source?:
    | 'legacy_deterministic'
    | 'legacy_deterministic_metric'
    | 'legacy_llm'
    | 'dsl_compiled'
    | 'dsl_corrected'
    | 'dsl_deterministic_metric'
    | 'dsl_deterministic_compare'
    | 'verified_exact'
    | 'legacy_fallback';
  query_dsl?: Record<string, unknown> | null;
  dsl_fallback_reason?: string | null;
  dsl_attempts?: number;
  sql_correction_attempts?: number;
  llm_calls?: number;
  token_usage?: TokenUsage;
  policy_version?: string;
  policy_hash?: string;
  policy_admin_bypass?: boolean;
  policy_domain?: string;
  policy_datasource?: string;
  query_set_id?: string;
  query_set_version?: number;
  query_set_hash?: string;
  semantic_release_id?: string;
  semantic_release_version?: number;
  business_rule_set_id?: string;
  business_rule_set_version?: number;
  semantic_term_matches?: SemanticTermMatch[];
  verified_query_match?: VerifiedQueryMatch;
  verified_query_examples?: VerifiedQueryMatch[];
  business_rule_matches?: BusinessRuleMatch[];
  conversation_id?: string | null;
  parent_trace_id?: string | null;
  regenerate_of_trace_id?: string | null;
  standalone_question?: string;
  context_inherited?: boolean;
  context_turns_used?: string[];
  context_resolution_confidence?: 'high' | 'low';
  context_ambiguity_reason?: string | null;
}

export interface WarningEvent {
  type: 'warning';
  stage?: string;
  message: string;
  request_id?: string;
}

export interface ClarificationEvent {
  type: 'clarification';
  message: string;
  standalone_question?: string;
  request_id?: string;
}

export interface RecommendationsEvent {
  type: 'recommendations';
  questions: string[];
  source: 'manual' | 'hybrid';
  llm_calls: number;
  request_id?: string;
}

export interface SqlEvent {
  type: 'sql';
  sql: string;
  status?: string;
  request_id?: string;
}

export interface ResultEvent {
  type: 'result';
  data: Row[];
  sql?: string;
  columns: string[];
  row_count: number;
  truncated: boolean;
  request_id?: string;
}

export interface AnswerEvent {
  type: 'answer';
  summary: string;
  row_count: number;
  columns: string[];
  metrics: string[];
  time_range: string;
  sql: string;
  request_id?: string;
}

export interface VisualizationEvent {
  type: 'visualization';
  chart_spec: ChartSpecV1;
  request_id?: string;
}

export interface ErrorEvent {
  type: 'error';
  stage?: string;
  message: string;
  request_id?: string;
}

export interface DoneEvent {
  type: 'done';
  status: 'completed' | 'failed' | 'needs_input';
  duration_ms: number;
  error: string | null;
  request_id?: string;
  llm_calls?: number;
  token_usage?: TokenUsage;
}

export type QueryEvent =
  | ProgressEvent
  | ContextEvent
  | WarningEvent
  | ClarificationEvent
  | RecommendationsEvent
  | SqlEvent
  | ResultEvent
  | AnswerEvent
  | VisualizationEvent
  | ErrorEvent
  | DoneEvent;

export const QUERY_EVENT_TYPES = new Set([
  'progress',
  'context',
  'warning',
  'clarification',
  'recommendations',
  'sql',
  'result',
  'answer',
  'visualization',
  'error',
  'done',
]);

/** Parse one SSE `data:` payload into a typed event, or null when malformed. */
export function parseQueryEvent(raw: string): QueryEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (
    typeof parsed !== 'object' ||
    parsed === null ||
    !('type' in parsed) ||
    typeof (parsed as { type: unknown }).type !== 'string' ||
    !QUERY_EVENT_TYPES.has((parsed as { type: string }).type)
  ) {
    return null;
  }
  return parsed as QueryEvent;
}
