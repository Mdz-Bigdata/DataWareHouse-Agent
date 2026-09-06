import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, ContentSwitcher, InlineNotification, Switch, Tag } from '@carbon/react';
import { Download, Maximize } from '@carbon/icons-react';
import {
  buildChartSpecForType,
  compatibleChartTypes,
  resolveChartSpec,
} from '../lib/chartDetection';
import { exportSvgAsPng } from '../lib/png';
import type { ChartSpecV1, ChartType, Row } from '../types/events';
import { MetricCard } from './MetricCard';
import { ResultChart } from './ResultChart';
import { ResultTable } from './ResultTable';

interface ResultVisualizationProps {
  chartSpec: ChartSpecV1 | null;
  columns: string[];
  rows: Row[];
  truncated: boolean;
  requestId: string | null;
  theme: 'g10' | 'g100';
}

const LABELS: Record<ChartType, string> = {
  table: '表格',
  kpi: '指标卡',
  bar: '柱状图',
  line: '折线图',
  pie: '饼图',
};

export function ResultVisualization({
  chartSpec,
  columns,
  rows,
  truncated,
  requestId,
  theme,
}: ResultVisualizationProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const proposed = useMemo(
    () => resolveChartSpec(chartSpec, columns, rows),
    [chartSpec, columns, rows],
  );
  const availableTypes = useMemo(() => compatibleChartTypes(columns, rows), [columns, rows]);
  const [selectedType, setSelectedType] = useState<ChartType>(proposed.type);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedType(proposed.type);
    setExportError(null);
  }, [proposed]);

  const selectedSpec =
    selectedType === proposed.type
      ? proposed
      : (buildChartSpecForType(selectedType, columns, rows) ?? proposed);
  const selectedIndex = Math.max(0, availableTypes.indexOf(selectedSpec.type));
  const chartVisible = ['bar', 'line', 'pie'].includes(selectedSpec.type);

  const enterFullscreen = async () => {
    if (!rootRef.current?.requestFullscreen) {
      setExportError('当前浏览器不支持全屏显示。');
      return;
    }
    try {
      setExportError(null);
      await rootRef.current.requestFullscreen();
    } catch (error) {
      setExportError(error instanceof Error ? error.message : '无法进入全屏显示');
    }
  };

  const exportPng = async () => {
    const svg = rootRef.current?.querySelector<SVGSVGElement>('.chart-canvas svg');
    if (!svg) {
      setExportError('图表尚未完成渲染，请稍后重试。');
      return;
    }
    try {
      setExportError(null);
      const stamp = requestId ? requestId.slice(0, 8) : String(Date.now());
      await exportSvgAsPng(
        svg,
        `listenbook-chart-${stamp}.png`,
        theme === 'g100' ? '#12293a' : '#ffffff',
      );
    } catch (error) {
      setExportError(error instanceof Error ? error.message : 'PNG 导出失败');
    }
  };

  return (
    <div ref={rootRef} className="result-visualization">
      <section className="panel visualization-toolbar" aria-label="结果展示方式">
        <div>
          <div className="visualization-title-row">
            <h2 className="panel-title">{selectedSpec.title}</h2>
            <Tag type="green" size="sm">
              ChartSpecV1
            </Tag>
          </div>
          <p className="muted visualization-hint">图表字段已由服务端按真实结果列校验。</p>
        </div>
        <div className="visualization-actions">
          <ContentSwitcher
            size="sm"
            selectedIndex={selectedIndex}
            onChange={({ index }) => {
              const type = typeof index === 'number' ? availableTypes[index] : undefined;
              if (type) setSelectedType(type);
            }}
          >
            {availableTypes.map((type) => (
              <Switch key={type} name={type} text={LABELS[type]} />
            ))}
          </ContentSwitcher>
          <Button
            kind="ghost"
            size="sm"
            renderIcon={Maximize}
            onClick={() => void enterFullscreen()}
          >
            全屏
          </Button>
          {chartVisible && (
            <Button kind="ghost" size="sm" renderIcon={Download} onClick={() => void exportPng()}>
              导出 PNG
            </Button>
          )}
        </div>
      </section>

      {exportError && (
        <InlineNotification
          kind="warning"
          lowContrast
          hideCloseButton
          title="图表操作未完成"
          subtitle={exportError}
        />
      )}

      {selectedSpec.type === 'kpi' && (
        <MetricCard
          label={selectedSpec.metrics[0] ?? '结果'}
          value={Number(rows[0]?.[selectedSpec.metrics[0]] ?? 0)}
        />
      )}
      {chartVisible && (
        <>
          <section className="panel chart-panel chart-canvas" aria-label={selectedSpec.title}>
            <ResultChart spec={selectedSpec} rows={rows} theme={theme} />
          </section>
          <ResultTable columns={columns} rows={rows} truncated={truncated} requestId={requestId} />
        </>
      )}
      {selectedSpec.type === 'table' && (
        <ResultTable columns={columns} rows={rows} truncated={truncated} requestId={requestId} />
      )}
    </div>
  );
}
