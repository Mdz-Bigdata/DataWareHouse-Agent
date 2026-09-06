import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  CopyButton,
  Pagination,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@carbon/react';
import { ArrowDown, ArrowUp, ArrowsVertical, DocumentExport } from '@carbon/icons-react';
import { copyText } from '../lib/clipboard';
import { downloadCsv, toCsv } from '../lib/csv';
import { formatCellValue } from '../lib/format';
import { compareValues, toggleDirection, type SortDirection } from '../lib/sort';
import type { Row } from '../types/events';

interface ResultTableProps {
  columns: string[];
  rows: Row[];
  truncated: boolean;
  requestId: string | null;
}

const PAGE_SIZES = [20, 50, 100];

export function ResultTable({ columns, rows, truncated, requestId }: ResultTableProps) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0]);

  // A new result set resets view state.
  useEffect(() => {
    setSortColumn(null);
    setSortDirection('asc');
    setPage(1);
  }, [rows]);

  const sortedRows = useMemo(() => {
    if (!sortColumn) return rows;
    const direction = sortDirection === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => direction * compareValues(a[sortColumn], b[sortColumn]));
  }, [rows, sortColumn, sortDirection]);

  const pageRows = useMemo(
    () => sortedRows.slice((page - 1) * pageSize, page * pageSize),
    [sortedRows, page, pageSize],
  );

  const onSort = (column: string) => {
    if (sortColumn === column) {
      setSortDirection(toggleDirection(sortDirection));
    } else {
      setSortColumn(column);
      setSortDirection('asc');
    }
  };

  const exportCsv = () => {
    const stamp = requestId ? requestId.slice(0, 8) : String(Date.now());
    downloadCsv(`listenbook-query-${stamp}.csv`, toCsv(columns, rows));
  };

  return (
    <section className="panel table-panel" aria-labelledby="table-title">
      <div className="table-toolbar">
        <h2 id="table-title" className="panel-title">
          数据表格 <span className="muted-inline">共 {rows.length} 行</span>
        </h2>
        <div className="table-actions">
          <CopyButton
            feedback="已复制"
            feedbackTimeout={2000}
            iconDescription="复制全部数据"
            onClick={() => void copyText(toCsv(columns, rows))}
          />
          <Button kind="ghost" size="sm" renderIcon={DocumentExport} onClick={exportCsv}>
            导出 CSV
          </Button>
        </div>
      </div>
      <div className="table-scroll">
        <Table size="sm" aria-label="查询结果表格" stickyHeader>
          <TableHead>
            <TableRow>
              {columns.map((column) => (
                <TableHeader
                  key={column}
                  aria-sort={
                    sortColumn === column
                      ? sortDirection === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : 'none'
                  }
                >
                  <button type="button" className="sort-header" onClick={() => onSort(column)}>
                    <span>{column}</span>
                    {sortColumn === column ? (
                      sortDirection === 'asc' ? (
                        <ArrowUp size={14} aria-label="升序" />
                      ) : (
                        <ArrowDown size={14} aria-label="降序" />
                      )
                    ) : (
                      <ArrowsVertical size={14} aria-hidden="true" className="sort-hint" />
                    )}
                  </button>
                </TableHeader>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {pageRows.map((row, rowIndex) => (
              <TableRow key={(page - 1) * pageSize + rowIndex}>
                {columns.map((column) => (
                  <TableCell key={column}>{formatCellValue(row[column])}</TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Pagination
        page={page}
        pageSize={pageSize}
        pageSizes={PAGE_SIZES}
        totalItems={rows.length}
        onChange={({ page: nextPage, pageSize: nextPageSize }) => {
          setPage(nextPage);
          setPageSize(nextPageSize);
        }}
        itemsPerPageText="每页行数"
        itemRangeText={(min, max, total) => `${min}-${max} / 共 ${total} 行`}
        pageRangeText={(current, total) => `${current} / ${total} 页`}
        backwardText="上一页"
        forwardText="下一页"
      />
      {truncated && <p className="table-note">结果已截断，最多返回 500 行。</p>}
    </section>
  );
}
