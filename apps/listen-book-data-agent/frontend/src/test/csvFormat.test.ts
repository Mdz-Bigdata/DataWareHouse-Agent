import { describe, expect, it } from 'vitest';
import { escapeCsvCell, toCsv } from '../lib/csv';
import { formatCellValue, formatClock, formatDuration, formatNumber } from '../lib/format';

describe('escapeCsvCell', () => {
  it('quotes cells containing commas, quotes or newlines', () => {
    expect(escapeCsvCell('a,b')).toBe('"a,b"');
    expect(escapeCsvCell('说"你好"')).toBe('"说""你好"""');
    expect(escapeCsvCell('line1\nline2')).toBe('"line1\nline2"');
  });

  it('neutralizes spreadsheet formula injection', () => {
    expect(escapeCsvCell('=1+1')).toBe("'=1+1");
    expect(escapeCsvCell('+SUM(A1)')).toBe("'+SUM(A1)");
    expect(escapeCsvCell('@mention')).toBe("'@mention");
    expect(escapeCsvCell('\t=cmd')).toBe("'\t=cmd");
    expect(escapeCsvCell('-2+3')).toBe("'-2+3");
  });

  it('leaves plain negative numbers untouched', () => {
    expect(escapeCsvCell('-123.45')).toBe('-123.45');
  });

  it('renders null as an empty cell', () => {
    expect(escapeCsvCell(null)).toBe('');
  });
});

describe('toCsv', () => {
  it('emits a BOM, CRLF line endings and a header row', () => {
    const csv = toCsv(['名称', '播放量'], [{ 名称: '专辑A', 播放量: 10 }]);
    expect(csv.startsWith('﻿')).toBe(true);
    expect(csv).toContain('名称,播放量\r\n专辑A,10');
  });

  it('protects every row against formula injection', () => {
    const csv = toCsv(['v'], [{ v: '=HYPERLINK("http://evil")' }]);
    expect(csv).toContain("'=HYPERLINK");
  });
});

describe('format helpers', () => {
  it('formats cell values with a null placeholder', () => {
    expect(formatCellValue(null)).toBe('-');
    expect(formatCellValue(0)).toBe('0');
    expect(formatCellValue('专辑')).toBe('专辑');
    expect(formatCellValue(true)).toBe('是');
  });

  it('groups large numbers for the metric card', () => {
    expect(formatNumber(1234567)).toBe('1,234,567');
  });

  it('formats durations in ms or seconds', () => {
    expect(formatDuration(0)).toBe('<1 ms');
    expect(formatDuration(812)).toBe('812 ms');
    expect(formatDuration(1523)).toBe('1.5 s');
    expect(formatDuration(null)).toBe('');
  });

  it('formats clock time as HH:MM:SS', () => {
    expect(formatClock(new Date(2026, 6, 17, 9, 5, 3).getTime())).toBe('09:05:03');
  });
});
