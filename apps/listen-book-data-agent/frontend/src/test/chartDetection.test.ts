import { describe, expect, it } from 'vitest';
import {
  compatibleChartTypes,
  detectResultView,
  isNumericValue,
  MAX_BAR_ROWS,
  resolveChartSpec,
} from '../lib/chartDetection';
import type { Row } from '../types/events';

function makeRows(count: number, factory: (index: number) => Row): Row[] {
  return Array.from({ length: count }, (_, index) => factory(index));
}

describe('isNumericValue', () => {
  it('accepts numbers and numeric strings (MySQL DECIMAL serializes as string)', () => {
    expect(isNumericValue(42)).toBe(true);
    expect(isNumericValue(-3.5)).toBe(true);
    expect(isNumericValue('12345.67')).toBe(true);
    expect(isNumericValue('-9')).toBe(true);
  });

  it('rejects text, null and NaN-like values', () => {
    expect(isNumericValue('专辑A')).toBe(false);
    expect(isNumericValue('')).toBe(false);
    expect(isNumericValue(null)).toBe(false);
    expect(isNumericValue(Number.NaN)).toBe(false);
  });
});

describe('detectResultView', () => {
  it('single row with a single numeric column → metric card', () => {
    const spec = detectResultView(['播放总量'], [{ 播放总量: '12345.67' }]);
    expect(spec.type).toBe('kpi');
    expect(spec.metrics).toEqual(['播放总量']);
    expect(spec.schema_version).toBe('chart-spec/v1');
  });

  it('single row with a non-numeric value → table', () => {
    expect(detectResultView(['名称'], [{ 名称: '专辑A' }]).type).toBe('table');
  });

  it('time column + numeric column → line chart', () => {
    const rows = makeRows(7, (i) => ({
      日期: `2026-07-${String(i + 10).padStart(2, '0')}`,
      播放量: 100 + i,
    }));
    const spec = detectResultView(['日期', '播放量'], rows);
    expect(spec.type).toBe('line');
    expect(spec.dimension).toBe('日期');
    expect(spec.metrics).toEqual(['播放量']);
  });

  it('detects time columns by values even without a time-ish name', () => {
    const rows = makeRows(3, (i) => ({ stat: `2026-0${i + 1}`, cnt: i }));
    expect(detectResultView(['stat', 'cnt'], rows).type).toBe('line');
  });

  it('accepts compact yyyymmdd only when the column name hints time', () => {
    const dated = makeRows(3, (i) => ({ 统计日期: `2026071${i}`, cnt: i }));
    expect(detectResultView(['统计日期', 'cnt'], dated).type).toBe('line');
    const ids = makeRows(3, (i) => ({ album_id: `1001234${i}`, cnt: i }));
    expect(detectResultView(['album_id', 'cnt'], ids).type).toBe('table');
  });

  it('text dimension + numeric column → horizontal bar', () => {
    const rows = makeRows(10, (i) => ({ 专辑名称: `专辑${i}`, 播放量: 1000 - i }));
    const spec = detectResultView(['专辑名称', '播放量'], rows);
    expect(spec.type).toBe('bar');
    expect(spec.dimension).toBe('专辑名称');
    expect(spec.metrics).toEqual(['播放量']);
    expect(compatibleChartTypes(['专辑名称', '播放量'], rows)).toEqual(['table', 'bar', 'pie']);
  });

  it('falls back to a table when there are too many bar categories', () => {
    const rows = makeRows(MAX_BAR_ROWS + 1, (i) => ({ name: `n${i}`, v: i }));
    expect(detectResultView(['name', 'v'], rows).type).toBe('table');
  });

  it('a single data row is not enough for a chart', () => {
    const spec = detectResultView(['日期', '播放量'], [{ 日期: '2026-07-10', 播放量: 1 }]);
    expect(spec.type).toBe('table');
  });

  it('ambiguous shapes (all text, or no clear dimension) stay tables', () => {
    const textOnly = makeRows(3, (i) => ({ a: `x${i}`, b: `y${i}` }));
    expect(detectResultView(['a', 'b'], textOnly).type).toBe('table');
    expect(detectResultView([], []).type).toBe('table');
  });

  it('supports multiple numeric series for line charts', () => {
    const rows = makeRows(4, (i) => ({ dt: `2026-01-0${i + 1}`, 播放量: i, 订单数: i * 2 }));
    const spec = detectResultView(['dt', '播放量', '订单数'], rows);
    expect(spec.type).toBe('line');
    expect(spec.metrics).toEqual(['播放量', '订单数']);
  });

  it('rejects a server spec that references a non-result field', () => {
    const rows = makeRows(3, (i) => ({ 渠道: `渠道${i}`, 播放量: i + 1 }));
    const resolved = resolveChartSpec(
      {
        schema_version: 'chart-spec/v1',
        type: 'bar',
        title: '越权图表',
        dimension: '渠道',
        metrics: ['手机号'],
        series: null,
        source: 'llm_validated',
      },
      ['渠道', '播放量'],
      rows,
    );
    expect(resolved.type).toBe('bar');
    expect(resolved.metrics).toEqual(['播放量']);
    expect(resolved.source).toBe('deterministic');
  });
});
