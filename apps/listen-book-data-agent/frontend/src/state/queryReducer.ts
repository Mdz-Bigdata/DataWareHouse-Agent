import type {
  AnalysisPlan,
  ChartSpecV1,
  ContextEvent,
  QueryEvent,
  QueryPlanV1,
  Row,
  TokenUsage,
} from '../types/events';
import { isChartSpecV1 } from '../lib/chartDetection';
import type { HistoryEntry } from './historyReducer';

/**
 * Single-query lifecycle:
 *   idle → connecting → streaming → completed
 *                                ├→ failed
 *                                └→ cancelled
 */
export type QueryPhase =
  | 'idle'
  | 'connecting'
  | 'streaming'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'needs_input'
  | 'restored';

export type StepStatus = 'running' | 'success' | 'error' | 'skipped' | 'degraded';

export interface StepState {
  name: string;
  status: StepStatus;
  durationMs: number | null;
}

export interface AnswerState {
  summary: string;
  metrics: string[];
  timeRange: string;
}

export interface QueryState {
  phase: QueryPhase;
  requestId: string | null;
  conversationId: string | null;
  parentTraceId: string | null;
  regenerateOfTraceId: string | null;
  question: string;
  standaloneQuestion: string | null;
  contextInherited: boolean;
  contextTurnsUsed: string[];
  clarification: string | null;
  recommendations: string[];
  recommendationSource: 'manual' | 'hybrid' | null;
  steps: StepState[];
  analysisPlan: AnalysisPlan | null;
  queryPlan: QueryPlanV1 | null;
  planningRoles: Array<'Selector' | 'Decomposer' | 'Refiner'>;
  selectedSemantics: NonNullable<ContextEvent['selected_semantics']> | null;
  decomposedQuery: Array<Record<string, unknown>>;
  queryPlanRefined: boolean;
  dryPlanStatus: 'validated' | null;
  dryPlanChecks: string[];
  sqlValidationStages: string[];
  explainEstimate: NonNullable<ContextEvent['explain_estimate']> | null;
  explainBudget: NonNullable<ContextEvent['explain_budget']> | null;
  executionMode: 'read_only' | null;
  executionTimeoutSeconds: number | null;
  tables: string[];
  warnings: string[];
  generationMode: 'legacy' | 'dsl' | null;
  generationSource: string | null;
  queryDsl: Record<string, unknown> | null;
  dslFallbackReason: string | null;
  dslAttempts: number;
  sqlCorrectionAttempts: number;
  llmCalls: number;
  tokenUsage: TokenUsage | null;
  buildId: string | null;
  policyVersion: string | null;
  policyHash: string | null;
  policyAdminBypass: boolean;
  querySetId: string | null;
  querySetVersion: number | null;
  semanticReleaseId: string | null;
  semanticReleaseVersion: number | null;
  businessRuleSetId: string | null;
  businessRuleSetVersion: number | null;
  semanticTermMatches: NonNullable<ContextEvent['semantic_term_matches']>;
  verifiedQueryMatch: NonNullable<ContextEvent['verified_query_match']> | null;
  verifiedQueryExamples: NonNullable<ContextEvent['verified_query_examples']>;
  businessRuleMatches: NonNullable<ContextEvent['business_rule_matches']>;
  sql: string | null;
  sqlStatus: string | null;
  columns: string[];
  rows: Row[];
  rowCount: number;
  truncated: boolean;
  answer: AnswerState | null;
  chartSpec: ChartSpecV1 | null;
  error: string | null;
  durationMs: number | null;
  startedAt: number | null;
}

export const initialQueryState: QueryState = {
  phase: 'idle',
  requestId: null,
  conversationId: null,
  parentTraceId: null,
  regenerateOfTraceId: null,
  question: '',
  standaloneQuestion: null,
  contextInherited: false,
  contextTurnsUsed: [],
  clarification: null,
  recommendations: [],
  recommendationSource: null,
  steps: [],
  analysisPlan: null,
  queryPlan: null,
  planningRoles: [],
  selectedSemantics: null,
  decomposedQuery: [],
  queryPlanRefined: false,
  dryPlanStatus: null,
  dryPlanChecks: [],
  sqlValidationStages: [],
  explainEstimate: null,
  explainBudget: null,
  executionMode: null,
  executionTimeoutSeconds: null,
  tables: [],
  warnings: [],
  generationMode: null,
  generationSource: null,
  queryDsl: null,
  dslFallbackReason: null,
  dslAttempts: 0,
  sqlCorrectionAttempts: 0,
  llmCalls: 0,
  tokenUsage: null,
  buildId: null,
  policyVersion: null,
  policyHash: null,
  policyAdminBypass: false,
  querySetId: null,
  querySetVersion: null,
  semanticReleaseId: null,
  semanticReleaseVersion: null,
  businessRuleSetId: null,
  businessRuleSetVersion: null,
  semanticTermMatches: [],
  verifiedQueryMatch: null,
  verifiedQueryExamples: [],
  businessRuleMatches: [],
  sql: null,
  sqlStatus: null,
  columns: [],
  rows: [],
  rowCount: 0,
  truncated: false,
  answer: null,
  chartSpec: null,
  error: null,
  durationMs: null,
  startedAt: null,
};

export type QueryAction =
  | { type: 'start'; question: string; startedAt: number }
  | { type: 'event'; event: QueryEvent }
  | { type: 'cancel' }
  | { type: 'fail'; message: string; durationMs: number }
  | { type: 'streamClosed'; sawDone: boolean }
  | { type: 'restore'; entry: HistoryEntry }
  | { type: 'reset' };

function isActive(phase: QueryPhase): boolean {
  return phase === 'connecting' || phase === 'streaming';
}

function upsertStep(
  steps: StepState[],
  name: string,
  status: StepStatus,
  durationMs: number | null,
): StepState[] {
  const index = steps.findIndex((step) => step.name === name);
  if (index === -1) return [...steps, { name, status, durationMs }];
  const next = steps.slice();
  next[index] = { name, status, durationMs: durationMs ?? next[index].durationMs };
  return next;
}

function applyEvent(state: QueryState, event: QueryEvent): QueryState {
  // The first event of any kind proves the connection is live.
  const streaming: QueryState =
    state.phase === 'connecting' ? { ...state, phase: 'streaming' } : state;
  switch (event.type) {
    case 'context':
      return {
        ...streaming,
        requestId: event.request_id || streaming.requestId,
        conversationId: event.conversation_id ?? streaming.conversationId,
        parentTraceId: event.parent_trace_id ?? streaming.parentTraceId,
        regenerateOfTraceId: event.regenerate_of_trace_id ?? streaming.regenerateOfTraceId,
        standaloneQuestion: event.standalone_question ?? streaming.standaloneQuestion,
        contextInherited: event.context_inherited ?? streaming.contextInherited,
        contextTurnsUsed: event.context_turns_used ?? streaming.contextTurnsUsed,
        analysisPlan: event.analysis_plan ?? streaming.analysisPlan,
        queryPlan: event.query_plan ?? streaming.queryPlan,
        planningRoles: event.planning_roles ?? streaming.planningRoles,
        selectedSemantics: event.selected_semantics ?? streaming.selectedSemantics,
        decomposedQuery: event.decomposed_query ?? streaming.decomposedQuery,
        queryPlanRefined: event.query_plan_refined ?? streaming.queryPlanRefined,
        dryPlanStatus: event.dry_plan_status ?? streaming.dryPlanStatus,
        dryPlanChecks: event.dry_plan_checks ?? streaming.dryPlanChecks,
        sqlValidationStages: event.sql_validation_stages ?? streaming.sqlValidationStages,
        explainEstimate: event.explain_estimate ?? streaming.explainEstimate,
        explainBudget: event.explain_budget ?? streaming.explainBudget,
        executionMode: event.execution_mode ?? streaming.executionMode,
        executionTimeoutSeconds:
          event.execution_timeout_seconds ?? streaming.executionTimeoutSeconds,
        tables: event.tables?.length ? event.tables : streaming.tables,
        warnings: event.warnings?.length
          ? [...new Set([...streaming.warnings, ...event.warnings])]
          : streaming.warnings,
        generationMode: event.generation_mode ?? streaming.generationMode,
        generationSource: event.generation_source ?? streaming.generationSource,
        queryDsl: event.query_dsl ?? streaming.queryDsl,
        dslFallbackReason: event.dsl_fallback_reason ?? streaming.dslFallbackReason,
        dslAttempts: event.dsl_attempts ?? streaming.dslAttempts,
        sqlCorrectionAttempts: event.sql_correction_attempts ?? streaming.sqlCorrectionAttempts,
        llmCalls: event.llm_calls ?? streaming.llmCalls,
        tokenUsage: event.token_usage ?? streaming.tokenUsage,
        buildId: event.build_id ?? streaming.buildId,
        policyVersion: event.policy_version ?? streaming.policyVersion,
        policyHash: event.policy_hash ?? streaming.policyHash,
        policyAdminBypass: event.policy_admin_bypass ?? streaming.policyAdminBypass,
        querySetId: event.query_set_id ?? streaming.querySetId,
        querySetVersion: event.query_set_version ?? streaming.querySetVersion,
        semanticReleaseId: event.semantic_release_id ?? streaming.semanticReleaseId,
        semanticReleaseVersion: event.semantic_release_version ?? streaming.semanticReleaseVersion,
        businessRuleSetId: event.business_rule_set_id ?? streaming.businessRuleSetId,
        businessRuleSetVersion: event.business_rule_set_version ?? streaming.businessRuleSetVersion,
        semanticTermMatches: event.semantic_term_matches ?? streaming.semanticTermMatches,
        verifiedQueryMatch: event.verified_query_match ?? streaming.verifiedQueryMatch,
        verifiedQueryExamples: event.verified_query_examples ?? streaming.verifiedQueryExamples,
        businessRuleMatches: event.business_rule_matches ?? streaming.businessRuleMatches,
      };
    case 'warning':
      return {
        ...streaming,
        warnings: [...new Set([...streaming.warnings, event.message])],
      };
    case 'clarification':
      return {
        ...streaming,
        clarification: event.message,
        standaloneQuestion: event.standalone_question ?? streaming.standaloneQuestion,
      };
    case 'recommendations':
      return {
        ...streaming,
        recommendations: event.questions,
        recommendationSource: event.source,
        llmCalls: Math.max(streaming.llmCalls, event.llm_calls),
      };
    case 'progress':
      return {
        ...streaming,
        steps: upsertStep(streaming.steps, event.step, event.status, event.duration_ms ?? null),
      };
    case 'sql':
      return { ...streaming, sql: event.sql, sqlStatus: event.status ?? 'validated' };
    case 'result':
      return {
        ...streaming,
        sql: event.sql ?? streaming.sql,
        columns: event.columns,
        rows: event.data,
        rowCount: event.row_count,
        truncated: event.truncated,
      };
    case 'answer':
      return {
        ...streaming,
        answer: {
          summary: event.summary,
          metrics: event.metrics,
          timeRange: event.time_range,
        },
      };
    case 'visualization':
      return { ...streaming, chartSpec: event.chart_spec };
    case 'error':
      return { ...streaming, error: event.message };
    case 'done':
      return {
        ...streaming,
        phase:
          event.status === 'completed'
            ? 'completed'
            : event.status === 'needs_input'
              ? 'needs_input'
              : 'failed',
        durationMs: event.duration_ms,
        error: event.error ?? streaming.error,
        llmCalls: Math.max(streaming.llmCalls, event.llm_calls ?? 0),
        tokenUsage: event.token_usage ?? streaming.tokenUsage,
      };
  }
}

export function queryReducer(state: QueryState, action: QueryAction): QueryState {
  switch (action.type) {
    case 'start':
      return {
        ...initialQueryState,
        phase: 'connecting',
        question: action.question,
        startedAt: action.startedAt,
      };
    case 'event':
      if (!isActive(state.phase)) return state;
      return applyEvent(state, action.event);
    case 'cancel':
      return isActive(state.phase) ? { ...state, phase: 'cancelled' } : state;
    case 'fail':
      if (!isActive(state.phase)) return state;
      return { ...state, phase: 'failed', error: action.message, durationMs: action.durationMs };
    case 'streamClosed':
      if (action.sawDone || !isActive(state.phase)) return state;
      return {
        ...state,
        phase: 'failed',
        error: '连接中断：未收到完成标记，已展示的结果可能不完整。',
      };
    case 'restore': {
      const entry = action.entry;
      const restoredPhase: QueryPhase =
        entry.status === 'completed'
          ? 'restored'
          : entry.status === 'needs_input'
            ? 'needs_input'
            : entry.status === 'running'
              ? 'cancelled'
              : entry.status;
      return {
        ...initialQueryState,
        phase: restoredPhase,
        requestId: entry.requestId,
        conversationId: entry.conversationId,
        parentTraceId: entry.parentTraceId,
        regenerateOfTraceId: entry.regenerateOfTraceId,
        question: entry.question,
        standaloneQuestion: entry.standaloneQuestion,
        analysisPlan: entry.analysisPlan,
        queryPlan: entry.queryPlan,
        buildId: entry.buildId,
        semanticReleaseId: entry.semanticReleaseId,
        semanticReleaseVersion: entry.semanticReleaseVersion,
        querySetId: entry.querySetId,
        querySetVersion: entry.querySetVersion,
        businessRuleSetId: entry.businessRuleSetId,
        businessRuleSetVersion: entry.businessRuleSetVersion,
        policyVersion: entry.policyVersion,
        policyHash: entry.policyHash,
        sql: entry.sql,
        sqlStatus: entry.sql ? 'validated' : null,
        answer: entry.answerSummary
          ? {
              summary: entry.answerSummary,
              metrics: entry.analysisPlan?.metric_hints ?? [],
              timeRange: entry.analysisPlan?.time_range.label ?? '未限定',
            }
          : null,
        chartSpec: isChartSpecV1(entry.chartSpec) ? entry.chartSpec : null,
        durationMs: entry.durationMs,
        startedAt: entry.startedAt,
        clarification: entry.status === 'needs_input' ? '该历史轮次等待补充查询条件。' : null,
        error: entry.status === 'failed' ? '该历史轮次执行失败。' : null,
      };
    }
    case 'reset':
      return initialQueryState;
  }
}
