import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  createConversation,
  fetchConversations,
  fetchConversationTurns,
  updateConversation,
  type Conversation,
} from '../lib/conversations';
import { isAbortError, runQuery } from '../lib/queryClient';
import type { QueryEvent } from '../types/events';
import { historyReducer, type HistoryEntry } from './historyReducer';
import { initialQueryState, queryReducer, type QueryState } from './queryReducer';

export interface QueryController {
  state: QueryState;
  history: HistoryEntry[];
  conversations: Conversation[];
  activeConversationId: string | null;
  isActive: boolean;
  run: (question: string) => Promise<void>;
  openInsight: (cardId: string, question: string) => Promise<void>;
  regenerate: (entry: HistoryEntry) => Promise<void>;
  cancel: () => void;
  reset: () => void;
  newConversation: () => Promise<void>;
  selectConversation: (conversationId: string) => Promise<void>;
  renameConversation: (conversationId: string, title: string) => Promise<void>;
  archiveConversation: (conversationId: string) => Promise<void>;
  restoreTurn: (entry: HistoryEntry) => void;
}

/** Coordinates owner-scoped conversations, branch selection and one active SSE stream. */
export function useQueryController(): QueryController {
  const [state, dispatch] = useReducer(queryReducer, initialQueryState);
  const [history, dispatchHistory] = useReducer(historyReducer, []);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const runIdRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const activeConversationIdRef = useRef<string | null>(null);
  const parentTraceIdRef = useRef<string | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    runIdRef.current += 1;
    abortRef.current?.abort();
    dispatch({ type: 'reset' });
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchConversations()
      .then(async (items) => {
        if (cancelled) return;
        setConversations(items);
        const first = items[0];
        if (!first) return;
        activeConversationIdRef.current = first.id;
        setActiveConversationId(first.id);
        const turns = await fetchConversationTurns(first.id);
        if (cancelled || activeConversationIdRef.current !== first.id) return;
        dispatchHistory({ type: 'load', entries: turns });
        const latest = turns[0];
        parentTraceIdRef.current = latest?.requestId ?? null;
        if (latest) dispatch({ type: 'restore', entry: latest });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const newConversation = useCallback(async () => {
    runIdRef.current += 1;
    abortRef.current?.abort();
    const conversation = await createConversation('新分析');
    activeConversationIdRef.current = conversation.id;
    parentTraceIdRef.current = null;
    setActiveConversationId(conversation.id);
    setConversations((current) => [conversation, ...current]);
    dispatchHistory({ type: 'clear' });
    dispatch({ type: 'reset' });
  }, []);

  const selectConversation = useCallback(async (conversationId: string) => {
    runIdRef.current += 1;
    abortRef.current?.abort();
    activeConversationIdRef.current = conversationId;
    parentTraceIdRef.current = null;
    setActiveConversationId(conversationId);
    dispatch({ type: 'reset' });
    const turns = await fetchConversationTurns(conversationId);
    if (activeConversationIdRef.current !== conversationId) return;
    dispatchHistory({ type: 'load', entries: turns });
    const latest = turns[0];
    parentTraceIdRef.current = latest?.requestId ?? null;
    if (latest) dispatch({ type: 'restore', entry: latest });
  }, []);

  const renameConversation = useCallback(async (conversationId: string, title: string) => {
    const normalized = title.trim();
    if (!normalized) return;
    const updated = await updateConversation(conversationId, { title: normalized });
    setConversations((current) =>
      current.map((conversation) => (conversation.id === conversationId ? updated : conversation)),
    );
  }, []);

  const archiveConversation = useCallback(async (conversationId: string) => {
    await updateConversation(conversationId, { status: 'archived' });
    setConversations((current) => current.filter((item) => item.id !== conversationId));
    if (activeConversationIdRef.current === conversationId) {
      runIdRef.current += 1;
      abortRef.current?.abort();
      activeConversationIdRef.current = null;
      parentTraceIdRef.current = null;
      setActiveConversationId(null);
      dispatchHistory({ type: 'clear' });
      dispatch({ type: 'reset' });
    }
  }, []);

  const restoreTurn = useCallback((entry: HistoryEntry) => {
    runIdRef.current += 1;
    abortRef.current?.abort();
    parentTraceIdRef.current = entry.requestId;
    dispatch({ type: 'restore', entry });
  }, []);

  const execute = useCallback(
    async (question: string, regenerateEntry?: HistoryEntry, insightCardId?: string) => {
      const trimmed = question.trim();
      if (!trimmed) return;

      let conversationId = activeConversationIdRef.current;
      if (!conversationId) {
        try {
          const created = await createConversation(trimmed.slice(0, 40));
          conversationId = created.id;
          activeConversationIdRef.current = created.id;
          parentTraceIdRef.current = null;
          setActiveConversationId(created.id);
          setConversations((current) => [created, ...current]);
          dispatchHistory({ type: 'clear' });
        } catch (error) {
          const message = error instanceof Error ? error.message : '创建会话失败，请稍后重试。';
          dispatch({ type: 'start', question: trimmed, startedAt: Date.now() });
          dispatch({ type: 'fail', message, durationMs: 0 });
          return;
        }
      }

      runIdRef.current += 1;
      const runId = runIdRef.current;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const startedAt = Date.now();
      const parentTraceId = regenerateEntry
        ? regenerateEntry.parentTraceId
        : parentTraceIdRef.current;

      dispatch({ type: 'start', question: trimmed, startedAt });
      dispatchHistory({
        type: 'add',
        id: runId,
        question: trimmed,
        startedAt,
        conversationId,
        parentTraceId,
        regenerateOfTraceId: regenerateEntry?.requestId ?? null,
      });

      const stale = () => runIdRef.current !== runId;
      let currentTraceId: string | null = null;
      const patchHistory = (patch: Parameters<typeof patchHistoryEntry>[2]) => {
        if (!stale()) patchHistoryEntry(dispatchHistory, runId, patch);
      };

      const handleEvent = (event: QueryEvent) => {
        if (stale()) return;
        dispatch({ type: 'event', event });
        if (event.type === 'context') {
          if (event.request_id) currentTraceId = event.request_id;
          patchHistory({
            requestId: event.request_id,
            ...(event.standalone_question ? { standaloneQuestion: event.standalone_question } : {}),
            ...(event.analysis_plan ? { analysisPlan: event.analysis_plan } : {}),
            ...(event.query_plan ? { queryPlan: event.query_plan } : {}),
            ...(event.build_id ? { buildId: event.build_id } : {}),
            ...(event.semantic_release_id ? { semanticReleaseId: event.semantic_release_id } : {}),
            ...(event.semantic_release_version !== undefined
              ? { semanticReleaseVersion: event.semantic_release_version }
              : {}),
            ...(event.query_set_id ? { querySetId: event.query_set_id } : {}),
            ...(event.query_set_version !== undefined
              ? { querySetVersion: event.query_set_version }
              : {}),
            ...(event.business_rule_set_id
              ? { businessRuleSetId: event.business_rule_set_id }
              : {}),
            ...(event.business_rule_set_version !== undefined
              ? { businessRuleSetVersion: event.business_rule_set_version }
              : {}),
            ...(event.policy_version ? { policyVersion: event.policy_version } : {}),
            ...(event.policy_hash ? { policyHash: event.policy_hash } : {}),
          });
        } else if (event.type === 'sql') {
          patchHistory({ sql: event.sql });
        } else if (event.type === 'result') {
          patchHistory({
            rowCount: event.row_count,
            ...(event.sql ? { sql: event.sql } : {}),
          });
        } else if (event.type === 'answer') {
          patchHistory({ answerSummary: event.summary, sql: event.sql });
        } else if (event.type === 'done') {
          patchHistory({
            status:
              event.status === 'completed'
                ? 'completed'
                : event.status === 'needs_input'
                  ? 'needs_input'
                  : 'failed',
            durationMs: event.duration_ms,
          });
          if (currentTraceId && event.status !== 'failed') {
            parentTraceIdRef.current = currentTraceId;
          }
        }
      };

      try {
        await runQuery({
          query: trimmed,
          conversationId,
          parentTraceId,
          regenerateTraceId: regenerateEntry?.requestId,
          insightCardId,
          signal: controller.signal,
          onEvent: handleEvent,
          onClose: (sawDone) => {
            if (stale() || controller.signal.aborted) return;
            dispatch({ type: 'streamClosed', sawDone });
            if (!sawDone) patchHistory({ status: 'failed', durationMs: Date.now() - startedAt });
          },
        });
        if (!stale()) {
          fetchConversations()
            .then(setConversations)
            .catch(() => undefined);
        }
      } catch (error) {
        if (stale()) return;
        if (isAbortError(error)) {
          dispatch({ type: 'cancel' });
          patchHistory({ status: 'cancelled', durationMs: Date.now() - startedAt });
        } else {
          const message = error instanceof Error ? error.message : '查询失败，请稍后重试。';
          dispatch({ type: 'fail', message, durationMs: Date.now() - startedAt });
          patchHistory({ status: 'failed', durationMs: Date.now() - startedAt });
        }
      }
    },
    [],
  );

  const run = useCallback((question: string) => execute(question), [execute]);
  const openInsight = useCallback(
    (cardId: string, question: string) => execute(question, undefined, cardId),
    [execute],
  );
  const regenerate = useCallback(
    (entry: HistoryEntry) => execute(entry.question, entry),
    [execute],
  );
  const isActive = state.phase === 'connecting' || state.phase === 'streaming';

  return {
    state,
    history,
    conversations,
    activeConversationId,
    isActive,
    run,
    openInsight,
    regenerate,
    cancel,
    reset,
    newConversation,
    selectConversation,
    renameConversation,
    archiveConversation,
    restoreTurn,
  };
}

function patchHistoryEntry(
  dispatch: React.Dispatch<import('./historyReducer').HistoryAction>,
  id: number,
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
  >,
) {
  dispatch({ type: 'patch', id, patch });
}
