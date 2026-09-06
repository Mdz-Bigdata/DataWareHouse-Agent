import { GroupedBarChart, LineChart, PieChart, SimpleBarChart } from '@carbon/charts-react';
import { ChartTheme, ScaleTypes, type ChartTabularData } from '@carbon/charts';
import type { ChartSpecV1, Row } from '../types/events';
import { isNumericValue } from '../lib/chartDetection';

interface ResultChartProps {
  spec: ChartSpecV1;
  rows: Row[];
  theme: 'g10' | 'g100';
}

const CHART_PALETTES = {
  g10: ['#046a38', '#86bc25', '#2c5234', '#009a44', '#62b5e5'],
  g100: ['#86bc25', '#50c7a5', '#9fd44d', '#62b5e5', '#3f6b4f'],
} as const;

function buildColorScale(groups: string[], palette: readonly string[]): Record<string, string> {
  return Object.fromEntries(groups.map((group, index) => [group, palette[index % palette.length]]));
}

/** Rows are mapped 1:1 into chart points and are never re-aggregated. */
export function ResultChart({ spec, rows, theme }: ResultChartProps) {
  const chartTheme = theme === 'g100' ? ChartTheme.G100 : ChartTheme.WHITE;
  const palette = CHART_PALETTES[theme];
  const dimension = spec.dimension;

  if (spec.type === 'line' && dimension) {
    const data: ChartTabularData = rows.flatMap((row) =>
      spec.metrics
        .filter((metric) => isNumericValue(row[metric]))
        .map((metric) => ({
          group: spec.series ? String(row[spec.series] ?? '（空）') : metric,
          key: String(row[dimension]),
          value: Number(row[metric]),
        })),
    );
    const groups = [...new Set(data.map((point) => String(point.group)))];
    return (
      <LineChart
        data={data}
        options={{
          title: '',
          theme: chartTheme,
          height: '360px',
          axes: {
            bottom: { title: dimension, mapsTo: 'key', scaleType: ScaleTypes.LABELS },
            left: { mapsTo: 'value', title: spec.metrics.join('、') },
          },
          color: { scale: buildColorScale(groups, palette) },
          legend: { enabled: groups.length > 1 },
          toolbar: { enabled: false },
        }}
      />
    );
  }

  if (spec.type === 'bar' && dimension) {
    const metric = spec.metrics[0];
    if (spec.series) {
      const data: ChartTabularData = rows
        .filter((row) => isNumericValue(row[metric]))
        .map((row) => ({
          group: String(row[spec.series ?? ''] ?? '（空）'),
          key: String(row[dimension] ?? '（空）'),
          value: Number(row[metric]),
        }));
      const groups = [...new Set(data.map((point) => String(point.group)))];
      return (
        <GroupedBarChart
          data={data}
          options={{
            title: '',
            theme: chartTheme,
            height: '400px',
            axes: {
              left: { mapsTo: 'value', title: metric },
              bottom: { mapsTo: 'key', title: dimension, scaleType: ScaleTypes.LABELS },
            },
            color: { scale: buildColorScale(groups, palette) },
            legend: { enabled: true },
            toolbar: { enabled: false },
          }}
        />
      );
    }
    const data: ChartTabularData = rows
      .filter((row) => isNumericValue(row[metric]))
      .map((row) => ({ group: String(row[dimension] ?? '（空）'), value: Number(row[metric]) }));
    const height = `${Math.min(640, Math.max(260, data.length * 34 + 80))}px`;
    return (
      <SimpleBarChart
        data={data}
        options={{
          title: '',
          theme: chartTheme,
          height,
          axes: {
            left: { mapsTo: 'value', title: metric },
            bottom: { mapsTo: 'group', scaleType: ScaleTypes.LABELS },
          },
          color: {
            scale: Object.fromEntries(data.map((point) => [String(point.group), palette[0]])),
          },
          legend: { enabled: false },
          toolbar: { enabled: false },
        }}
      />
    );
  }

  if (spec.type === 'pie' && dimension) {
    const metric = spec.metrics[0];
    const data: ChartTabularData = rows
      .filter((row) => isNumericValue(row[metric]))
      .map((row) => ({ group: String(row[dimension] ?? '（空）'), value: Number(row[metric]) }));
    return (
      <PieChart
        data={data}
        options={{
          title: '',
          theme: chartTheme,
          height: '400px',
          color: {
            scale: buildColorScale(
              data.map((point) => String(point.group)),
              palette,
            ),
          },
          legend: { enabled: true },
          toolbar: { enabled: false },
        }}
      />
    );
  }

  return null;
}
