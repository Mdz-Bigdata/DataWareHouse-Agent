import { describe, expect, it } from 'vitest';
import { historyReducer, type HistoryEntry } from '../state/historyReducer';

const loaded: HistoryEntry[] = [
  {
    id: -1,
    requestId: 'trace-1',
    conversationId: 'conversation-1',
    parentTraceId: null,
    regenerateOfTraceId: null,
    question: '历史问题一',
    standaloneQuestion: '历史问题一',
    status: 'completed',
    rowCount: null,
    durationMs: 120,
    startedAt: 1720000000000,
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
  },
];

describe('historyReducer load 动作', () => {
  it('用服务端记录替换本地历史', () => {
    const state = historyReducer([], { type: 'load', entries: loaded });
    expect(state).toHaveLength(1);
    expect(state[0].requestId).toBe('trace-1');
  });

  it('load 后新增会话记录仍然置顶', () => {
    const state = historyReducer(loaded, {
      type: 'add',
      id: 1,
      question: '新问题',
      startedAt: 1720000001000,
      conversationId: 'conversation-1',
      parentTraceId: 'trace-1',
    });
    expect(state[0].question).toBe('新问题');
    expect(state[1].question).toBe('历史问题一');
  });

  it('clear 清空全部记录', () => {
    expect(historyReducer(loaded, { type: 'clear' })).toHaveLength(0);
  });
});
