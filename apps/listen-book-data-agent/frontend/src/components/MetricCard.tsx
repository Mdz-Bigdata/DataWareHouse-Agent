import { formatNumber } from '../lib/format';

interface MetricCardProps {
  label: string;
  value: number;
}

/** Single-row, single-value results render as the headline metric. */
export function MetricCard({ label, value }: MetricCardProps) {
  return (
    <div className="panel metric-card" aria-label={`核心指标：${label}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{formatNumber(value)}</span>
    </div>
  );
}
