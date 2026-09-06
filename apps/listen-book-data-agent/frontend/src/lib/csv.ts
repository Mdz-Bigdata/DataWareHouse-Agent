import type { Row } from '../types/events';

/**
 * CSV export with spreadsheet formula-injection protection: any cell whose
 * text starts with =, +, @, tab/CR (or `-` unless it is a plain number) is
 * prefixed with a single quote so Excel/WPS renders it as text.
 */
const PLAIN_NUMBER = /^-\d+(\.\d+)?$/;

export function escapeCsvCell(value: unknown): string {
  let text = value === null || value === undefined ? '' : String(value);
  const dangerous =
    text.length > 0 &&
    (/^[=+@\t\r]/.test(text) || (text.startsWith('-') && !PLAIN_NUMBER.test(text)));
  if (dangerous) text = `'${text}`;
  if (/[",\n\r]/.test(text)) text = `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function toCsv(columns: string[], rows: Row[]): string {
  const lines = [columns.map(escapeCsvCell).join(',')];
  for (const row of rows) {
    lines.push(columns.map((column) => escapeCsvCell(row[column])).join(','));
  }
  // BOM keeps Excel from mis-decoding UTF-8 Chinese text.
  // eslint-disable-next-line no-irregular-whitespace -- 故意加 UTF-8 BOM（\uFEFF）
  return `﻿${lines.join('\r\n')}`;
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
