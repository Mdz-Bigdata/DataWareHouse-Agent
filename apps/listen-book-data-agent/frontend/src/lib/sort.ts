import type { RowValue } from '../types/events';
import { isNumericValue } from './chartDetection';

/** Numeric-aware cell comparison; null/undefined always sort last. */
export function compareValues(a: RowValue | undefined, b: RowValue | undefined): number {
  const aEmpty = a === null || a === undefined;
  const bEmpty = b === null || b === undefined;
  if (aEmpty && bEmpty) return 0;
  if (aEmpty) return 1;
  if (bEmpty) return -1;
  if (isNumericValue(a) && isNumericValue(b)) {
    return Number(a) - Number(b);
  }
  return String(a).localeCompare(String(b), 'zh');
}

export type SortDirection = 'asc' | 'desc';

export function toggleDirection(current: SortDirection): SortDirection {
  return current === 'asc' ? 'desc' : 'asc';
}
