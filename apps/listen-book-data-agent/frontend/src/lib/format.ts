import type { RowValue } from '../types/events';

/** Display a raw cell value; null renders as a dash placeholder. */
export function formatCellValue(value: RowValue | undefined): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

/** Locale-aware grouping for the metric card and large numbers. */
export function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value);
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return '';
  if (ms < 1) return '<1 ms';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** HH:MM:SS clock time for history entries. */
export function formatClock(timestamp: number): string {
  const date = new Date(timestamp);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
