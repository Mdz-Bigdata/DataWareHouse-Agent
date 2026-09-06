import { describe, expect, it } from 'vitest';
import { initialQueryState, queryReducer, type QueryState } from '../state/queryReducer';
import type { QueryEvent } from '../types/events';

function started(): QueryState {
  return queryReducer(initialQueryState, {
    type: 'start',
    question: '最近7天播放量',
    startedAt: 1000,
  });
}

function apply(state: QueryState, events: QueryEvent[]): QueryState {
  return events.reduce((current, event) => queryReducer(current, { type: 'event', event }), state);
}

const fullFlow: QueryEvent[] = [
  { type: 'context', request_id: 'req-1' },
  { type: 'progress', step: '分析问题', status: 'running' },
  {
    type: 'context',
    request_id: 'req-1',
    analysis_plan: {
      intent: 'aggregate',
      metric_hints: ['播放量'],
      dimensions: [],
      filters: [],
      time_range: { start: '2026-07-10', end: '2026-07-17', label: '最近7天' },
      time_grain: null,
      top_n: null,
      sort_direction: null,
      comparison: null,
    },
    tables: ['dw_play_record'],
    warnings: [],
  },
  { type: 'progress', step: '分析问题', status: 'success' },
  { type: 'sql', sql: 'select sum(play_cnt) from dw_play_record', status: 'validated' },
  {
    type: 'result',
    data: [{ total: 12345 }],
    sql: 'select sum(play_cnt) from dw_play_record',
    columns: ['total'],
    row_count: 1,
    truncated: false,
  },
  {
    type: 'answer',
    summary: '已执行查询，返回 1 行。',
    row_count: 1,
    columns: ['total'],
    metrics: ['播放量'],
    time_range: '最近7天',
    sql: 'select sum(play_cnt) from dw_play_record',
  },
  {
    type: 'visualization',
    chart_spec: {
      schema_version: 'chart-spec/v1',
      type: 'kpi',
      title: 'total',
      dimension: null,
      metrics: ['total'],
      series: null,
      source: 'deterministic',
    },
  },
  { type: 'done', status: 'completed', duration_ms: 812, error: null },
];

describe('queryReducer lifecycle', () => {
  it('walks idle → connecting → streaming → completed', () => {
    expect(initialQueryState.phase).toBe('idle');
    let state = started();
    expect(state.phase).toBe('connecting');
    state = apply(state, [fullFlow[0]]);
    expect(state.phase).toBe('streaming');
    expect(state.requestId).toBe('req-1');
    state = apply(state, fullFlow.slice(1));
    expect(state.phase).toBe('completed');
    expect(state.durationMs).toBe(812);
    expect(state.sql).toContain('select');
    expect(state.rowCount).toBe(1);
    expect(state.answer?.metrics).toEqual(['播放量']);
    expect(state.chartSpec?.type).toBe('kpi');
    expect(state.analysisPlan?.intent).toBe('aggregate');
    expect(state.tables).toEqual(['dw_play_record']);
  });

  it('upserts progress steps by name instead of duplicating them', () => {
    const state = apply(started(), [
      { type: 'progress', step: '生成SQL', status: 'running' },
      { type: 'progress', step: '执行SQL', status: 'running' },
      { type: 'progress', step: '生成SQL', status: 'success' },
    ]);
    expect(state.steps).toEqual([
      { name: '生成SQL', status: 'success', durationMs: null },
      { name: '执行SQL', status: 'running', durationMs: null },
    ]);
  });

  it('marks failed when an error event is followed by done(failed)', () => {
    const state = apply(started(), [
      { type: 'error', stage: 'execution', message: 'SQL 执行超时' },
      { type: 'done', status: 'failed', duration_ms: 300, error: 'SQL 执行超时' },
    ]);
    expect(state.phase).toBe('failed');
    expect(state.error).toBe('SQL 执行超时');
    expect(state.durationMs).toBe(300);
  });

  it('keeps partial data visible on failure', () => {
    const state = apply(started(), [
      { type: 'sql', sql: 'select 1', status: 'validated' },
      { type: 'error', message: 'boom' },
      { type: 'done', status: 'failed', duration_ms: 10, error: 'boom' },
    ]);
    expect(state.phase).toBe('failed');
    expect(state.sql).toBe('select 1');
  });

  it('cancels only from an active phase', () => {
    const active = queryReducer(started(), { type: 'cancel' });
    expect(active.phase).toBe('cancelled');
    const done = queryReducer(apply(started(), fullFlow), { type: 'cancel' });
    expect(done.phase).toBe('completed');
  });

  it('fails with an honest message when the stream closes without done', () => {
    const state = queryReducer(apply(started(), [fullFlow[0]]), {
      type: 'streamClosed',
      sawDone: false,
    });
    expect(state.phase).toBe('failed');
    expect(state.error).toContain('连接中断');
  });

  it('ignores streamClosed after a done event', () => {
    const completed = apply(started(), fullFlow);
    const state = queryReducer(completed, { type: 'streamClosed', sawDone: true });
    expect(state.phase).toBe('completed');
  });

  it('drops events that arrive after the run finished (stale guard)', () => {
    const cancelled = queryReducer(started(), { type: 'cancel' });
    const after = queryReducer(cancelled, { type: 'event', event: fullFlow[4] });
    expect(after.sql).toBeNull();
    expect(after.phase).toBe('cancelled');
  });

  it('merges sparse context events without dropping earlier data', () => {
    const state = apply(started(), [
      fullFlow[0],
      fullFlow[2], // context with plan + tables
      { type: 'context', request_id: 'req-1', warnings: ['字段召回为空'] },
    ]);
    expect(state.analysisPlan?.intent).toBe('aggregate');
    expect(state.tables).toEqual(['dw_play_record']);
    expect(state.warnings).toEqual(['字段召回为空']);
  });

  it('keeps DSL execution metadata from context events', () => {
    const state = apply(started(), [
      fullFlow[0],
      {
        type: 'context',
        request_id: 'req-1',
        generation_mode: 'dsl',
        generation_source: 'dsl_compiled',
        query_dsl: { version: '1', intent: 'aggregate' },
        dsl_attempts: 1,
        llm_calls: 1,
        build_id: 'build-1',
        policy_version: 'policy-v2',
        policy_hash: 'hash-v2',
        query_set_id: 'query-set-3',
        query_set_version: 3,
        semantic_term_matches: [
          {
            term_key: 'play_count',
            standard_term: '播放次数',
            version: 1,
            bindings: [{ kind: 'metric', semantic_id: 'play_count' }],
          },
        ],
        verified_query_examples: [{ case_key: 'play-count-case' }],
        business_rule_matches: [
          { rule_key: 'exclude_test', version: 2, rule_type: 'metric_constraint' },
        ],
      },
      {
        type: 'context',
        request_id: 'req-1',
        generation_source: 'legacy_fallback',
        dsl_fallback_reason: '指标不支持分组对比',
      },
    ]);

    expect(state.generationMode).toBe('dsl');
    expect(state.generationSource).toBe('legacy_fallback');
    expect(state.queryDsl).toEqual({ version: '1', intent: 'aggregate' });
    expect(state.dslFallbackReason).toContain('不支持');
    expect(state.buildId).toBe('build-1');
    expect(state.policyVersion).toBe('policy-v2');
    expect(state.querySetVersion).toBe(3);
    expect(state.semanticTermMatches[0].standard_term).toBe('播放次数');
    expect(state.verifiedQueryExamples[0].case_key).toBe('play-count-case');
    expect(state.businessRuleMatches[0].rule_key).toBe('exclude_test');
  });

  it('resets back to idle', () => {
    const state = queryReducer(apply(started(), fullFlow), { type: 'reset' });
    expect(state).toEqual(initialQueryState);
  });
});
