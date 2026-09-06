import type { ChartSpecV1, ChartType, Row } from '../types/events';

/** Bar and pie charts become unreadable above these category counts. */
export const MAX_BAR_ROWS = 30;
export const MAX_PIE_ROWS = 12;

const NUMERIC_PATTERN = /^-?\d+(\.\d+)?$/;
const TIME_NAME_PATTERN = /(日期|时间|月份|时刻|date|time|day|month|dt)/i;
const DATE_VALUE_PATTERNS = [
  /^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2})?)?$/,
  /^\d{4}\/\d{2}(\/\d{2})?$/,
];
const COMPACT_DATE_PATTERN = /^\d{8}$/;

export function isNumericValue(value: unknown): boolean {
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value === 'string') return NUMERIC_PATTERN.test(value.trim());
  return false;
}

function isNumericColumn(column: string, rows: Row[]): boolean {
  const values = rows
    .map((row) => row[column])
    .filter((value) => value !== null && value !== undefined);
  return values.length > 0 && values.every(isNumericValue);
}

function isTimeColumn(column: string, rows: Row[]): boolean {
  const nameHint = TIME_NAME_PATTERN.test(column);
  const values = rows
    .map((row) => row[column])
    .filter((value) => value !== null && value !== undefined);
  return (
    values.length > 0 &&
    values.every((value) => {
      const text = String(value).trim();
      return (
        DATE_VALUE_PATTERNS.some((pattern) => pattern.test(text)) ||
        (nameHint && COMPACT_DATE_PATTERN.test(text))
      );
    })
  );
}

function baseSpec(type: ChartType, title: string): ChartSpecV1 {
  return {
    schema_version: 'chart-spec/v1',
    type,
    title,
    dimension: null,
    metrics: [],
    series: null,
    source: 'deterministic',
  };
}

export function compatibleChartTypes(columns: string[], rows: Row[]): ChartType[] {
  const types: ChartType[] = ['table'];
  if (!columns.length || !rows.length) return types;
  const numericColumns = columns.filter((column) => isNumericColumn(column, rows));
  if (rows.length === 1 && columns.length === 1 && numericColumns.length === 1) {
    types.push('kpi');
  }
  const timeColumn = columns.find((column) => isTimeColumn(column, rows));
  if (rows.length >= 2 && timeColumn && numericColumns.some((column) => column !== timeColumn)) {
    types.push('line');
  }
  const dimension = columns.find((column) => !numericColumns.includes(column));
  if (rows.length >= 2 && dimension && numericColumns.length && rows.length <= MAX_BAR_ROWS) {
    types.push('bar');
  }
  if (rows.length >= 2 && dimension && numericColumns.length && rows.length <= MAX_PIE_ROWS) {
    types.push('pie');
  }
  return types;
}

export function buildChartSpecForType(
  type: ChartType,
  columns: string[],
  rows: Row[],
): ChartSpecV1 | null {
  if (!compatibleChartTypes(columns, rows).includes(type)) return null;
  if (type === 'table') return baseSpec('table', '数据表格');
  const numericColumns = columns.filter((column) => isNumericColumn(column, rows));
  if (type === 'kpi') {
    return {
      ...baseSpec('kpi', columns[0]),
      metrics: [columns[0]],
    };
  }
  if (type === 'line') {
    const dimension = columns.find((column) => isTimeColumn(column, rows));
    if (!dimension) return null;
    return {
      ...baseSpec('line', `${dimension}趋势`),
      dimension,
      metrics: numericColumns.filter((column) => column !== dimension).slice(0, 8),
    };
  }
  const dimension = columns.find((column) => !numericColumns.includes(column));
  if (!dimension || !numericColumns.length) return null;
  return {
    ...baseSpec(type, `${numericColumns[0]}按${dimension}${type === 'pie' ? '占比' : '对比'}`),
    dimension,
    metrics: [numericColumns[0]],
  };
}

/** Client fallback used before an SSE visualization event arrives. */
export function detectResultView(columns: string[], rows: Row[]): ChartSpecV1 {
  const types = compatibleChartTypes(columns, rows);
  const preferred: ChartType = types.includes('kpi')
    ? 'kpi'
    : types.includes('line')
      ? 'line'
      : types.includes('bar')
        ? 'bar'
        : 'table';
  return buildChartSpecForType(preferred, columns, rows) ?? baseSpec('table', '数据表格');
}

export function isChartSpecV1(value: unknown): value is ChartSpecV1 {
  if (!value || typeof value !== 'object') return false;
  const spec = value as Partial<ChartSpecV1>;
  return (
    spec.schema_version === 'chart-spec/v1' &&
    ['table', 'kpi', 'bar', 'line', 'pie'].includes(String(spec.type)) &&
    typeof spec.title === 'string' &&
    (typeof spec.dimension === 'string' || spec.dimension === null) &&
    Array.isArray(spec.metrics) &&
    spec.metrics.every((metric) => typeof metric === 'string') &&
    (typeof spec.series === 'string' || spec.series === null) &&
    ['deterministic', 'llm_validated'].includes(String(spec.source))
  );
}

export function resolveChartSpec(proposed: unknown, columns: string[], rows: Row[]): ChartSpecV1 {
  if (isChartSpecV1(proposed)) {
    const compatible = compatibleChartTypes(columns, rows);
    const available = new Set(columns);
    const references = [proposed.dimension, proposed.series, ...proposed.metrics].filter(
      (value): value is string => Boolean(value),
    );
    if (compatible.includes(proposed.type) && references.every((value) => available.has(value))) {
      return proposed;
    }
  }
  return detectResultView(columns, rows);
}
