/**
 * Owner-scoped conversation turns. Result rows remain request-local and are
 * deliberately absent when a persisted turn is restored.
 */
import type { AnalysisPlan, QueryPlanV1 } from '../types/events';

export interface HistoryEntry {
  id: number;
  requestId: string | null;
  conversationId: string | null;
  parentTraceId: string | null;
  regenerateOfTraceId: string | null;
  question: string;
  standaloneQuestion: string | null;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'needs_input';
  rowCount: number | null;
  durationMs: number | null;
  startedAt: number;
  analysisPlan: AnalysisPlan | null;
  queryPlan: QueryPlanV1 | null;
  answerSummary: string | null;
  chartSpec: Record<string, unknown> | null;
  sql: string | null;
  buildId: string | null;
  semanticReleaseId: string | null;
  semanticReleaseVersion: number | null;
  querySetId: string | null;
  querySetVersion: number | null;
  businessRuleSetId: string | null;
  businessRuleSetVersion: number | null;
  policyVersion: string | null;
  policyHash: string | null;
}

export type HistoryAction =
  | {
      type: 'add';
      id: number;
      question: string;
      startedAt: number;
      conversationId: string | null;
      parentTraceId: string | null;
      regenerateOfTraceId?: string | null;
    }
  | {
      type: 'patch';
      id: number;
      patch: Partial<
        Pick<
          HistoryEntry,
          | 'requestId'
          | 'status'
          | 'rowCount'
          | 'durationMs'
          | 'standaloneQuestion'
          | 'analysisPlan'
          | 'queryPlan'
          | 'answerSummary'
          | 'sql'
          | 'buildId'
          | 'semanticReleaseId'
          | 'semanticReleaseVersion'
          | 'querySetId'
          | 'querySetVersion'
          | 'businessRuleSetId'
          | 'businessRuleSetVersion'
          | 'policyVersion'
          | 'policyHash'
        >
      >;
    }
  | { type: 'load'; entries: HistoryEntry[] }
  | { type: 'clear' };

const MAX_ENTRIES = 50;

export function historyReducer(state: HistoryEntry[], action: HistoryAction): HistoryEntry[] {
  switch (action.type) {
    case 'add': {
      const entry: HistoryEntry = {
        id: action.id,
        requestId: null,
        conversationId: action.conversationId,
        parentTraceId: action.parentTraceId,
        regenerateOfTraceId: action.regenerateOfTraceId ?? null,
        question: action.question,
        standaloneQuestion: null,
        status: 'running',
        rowCount: null,
        durationMs: null,
        startedAt: action.startedAt,
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
      };
      return [entry, ...state].slice(0, MAX_ENTRIES);
    }
    case 'patch':
      return state.map((entry) => (entry.id === action.id ? { ...entry, ...action.patch } : entry));
    case 'load':
      return action.entries.slice(0, MAX_ENTRIES);
    case 'clear':
      return [];
  }
}
