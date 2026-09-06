import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Button,
  InlineNotification,
  SkeletonText,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
  CopyButton,
} from '@carbon/react';
import { ArrowLeft, ChevronRight, Renew } from '@carbon/icons-react';
import {
  fetchAllTraces,
  fetchAnalyticsStats,
  type AnalyticsStats,
  type TraceDetail,
} from '../lib/analytics';
import { formatDuration } from '../lib/format';
import { copyText } from '../lib/clipboard';

type StatusFilter = 'all' | 'completed' | 'failed';

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'completed', label: '成功' },
  { key: 'failed', label: '失败' },
];

const EMPTY_HINTS: Record<StatusFilter, string> = {
  all: '近 7 天还没有任何查询记录。',
  completed: '近 7 天没有执行成功的查询。',
  failed: '近 7 天没有失败的查询，运行良好。',
};

/** 查询分析后台（admin）：统计概览 + 失败分布 + 耗时分布 + 查询明细。 */
export function AdminAnalyticsPage() {
  const [stats, setStats] = useState<AnalyticsStats | null>(null);
  const [traces, setTraces] = useState<TraceDetail[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [expandedTrace, setExpandedTrace] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const [s, t] = await Promise.all([
        fetchAnalyticsStats(7),
        fetchAllTraces(100, statusFilter === 'all' ? undefined : statusFilter),
      ]);
      setStats(s);
      setTraces(t);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '加载失败');
    }
  }, [statusFilter]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="admin-page">
      <header className="admin-header">
        <Link to="/" className="admin-back">
          <ArrowLeft size={16} aria-hidden />
          返回工作台
        </Link>
        <div className="admin-title-row">
          <div>
            <h1 className="admin-title">查询分析</h1>
            <p className="admin-subtitle">近 7 天的查询执行情况：成功率、耗时分布与明细追踪。</p>
          </div>
          <Button kind="tertiary" size="sm" renderIcon={Renew} onClick={() => void reload()}>
            刷新
          </Button>
        </div>
      </header>

      {loadError && (
        <div className="admin-feedback-wrap">
          <InlineNotification
            kind="error"
            lowContrast
            title="加载失败"
            subtitle={loadError}
            hideCloseButton
          />
        </div>
      )}

      {!stats && !loadError && (
        <div className="admin-panel-wide">
          <SkeletonText paragraph lineCount={8} />
        </div>
      )}

      {stats && (
        <>
          {/* 模块 1：统计概览卡片 */}
          <OverviewCards overview={stats.overview} />

          {/* 模块 2：失败原因分布 */}
          <FailureReasons reasons={stats.failure_reasons} />

          {/* 模块 3：耗时分布 + 阶段排行 */}
          <DurationAnalysis buckets={stats.duration_buckets} phases={stats.phase_stats} />

          {/* 模块 4：查询明细表格 */}
          <TraceTable
            traces={traces}
            statusFilter={statusFilter}
            onFilterChange={setStatusFilter}
            expandedTrace={expandedTrace}
            onToggleExpand={setExpandedTrace}
          />
        </>
      )}
    </div>
  );
}

/** 模块 1：统计卡片（咨询风指标卡 + 每日趋势折叠表）。 */
interface StatCard {
  label: string;
  value: string;
  tone: 'neutral' | 'good' | 'bad';
  hint: string;
  /** 0-100，存在时在数值下渲染一条细进度条。 */
  meter?: number;
}

function OverviewCards({ overview }: { overview: AnalyticsStats['overview'] }) {
  const unfinished = Math.max(0, overview.total - overview.completed - overview.failed);
  const unfinishedHint = unfinished > 0 ? ` · 未完成/取消 ${unfinished}` : '';
  const cards: StatCard[] = [
    {
      label: '总查询数',
      value: String(overview.total),
      tone: 'neutral',
      hint: `成功 ${overview.completed} · 失败 ${overview.failed}${unfinishedHint}`,
    },
    {
      label: '执行成功率',
      value: `${overview.success_rate}%`,
      tone: overview.success_rate >= 80 ? 'good' : 'bad',
      hint: `${overview.completed} / ${overview.total} 查询成功`,
      meter: overview.success_rate,
    },
    {
      label: '失败数',
      value: String(overview.failed),
      tone: overview.failed === 0 ? 'good' : 'bad',
      hint:
        overview.failed > 0
          ? '需要关注'
          : unfinished > 0
            ? `无执行失败，另有 ${unfinished} 条未完成或取消`
            : '运行平稳',
    },
    {
      label: '平均耗时',
      value: overview.avg_duration_ms ? formatDuration(overview.avg_duration_ms) : '-',
      tone: 'neutral',
      hint: '单次查询平均水平',
    },
  ];
  return (
    <section className="analytics-overview">
      <h2 className="panel-title">统计概览 · 近 7 天</h2>
      <div className="analytics-cards">
        {cards.map((card) => (
          <div key={card.label} className={`stat-card stat-card--${card.tone}`}>
            <span className="stat-card-label">{card.label}</span>
            <span className="stat-card-value">{card.value}</span>
            {card.meter !== undefined && (
              <span className="stat-card-meter" aria-hidden>
                <span style={{ width: `${Math.min(100, Math.max(0, card.meter))}%` }} />
              </span>
            )}
            <span className="stat-card-hint">{card.hint}</span>
          </div>
        ))}
      </div>
      {overview.daily_stats.length > 0 && (
        <details className="analytics-daily">
          <summary>每日趋势</summary>
          <div className="analytics-table">
            <Table size="sm">
              <TableHead>
                <TableRow>
                  <TableHeader>日期</TableHeader>
                  <TableHeader>总数</TableHeader>
                  <TableHeader>成功</TableHeader>
                  <TableHeader>失败</TableHeader>
                  <TableHeader>成功率</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {overview.daily_stats.map((d) => (
                  <TableRow key={d.date}>
                    <TableCell>{d.date}</TableCell>
                    <TableCell className="analytics-num">{d.total}</TableCell>
                    <TableCell className="analytics-num">{d.completed}</TableCell>
                    <TableCell className="analytics-num">{d.failed}</TableCell>
                    <TableCell className="analytics-num">{d.success_rate}%</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </details>
      )}
    </section>
  );
}

/** 模块 2：失败原因分布（占比用红色行内条图）。 */
function FailureReasons({ reasons }: { reasons: AnalyticsStats['failure_reasons'] }) {
  if (reasons.length === 0) {
    return (
      <section className="analytics-section">
        <h2 className="panel-title">失败原因分布</h2>
        <div className="analytics-empty">
          <p className="analytics-empty-title">暂无失败原因</p>
          <p className="analytics-empty-hint">进行中或被取消的查询不计入失败原因。</p>
        </div>
      </section>
    );
  }
  const total = reasons.reduce((sum, r) => sum + r.count, 0);
  return (
    <section className="analytics-section">
      <h2 className="panel-title">失败原因分布</h2>
      <div className="analytics-table">
        <Table size="sm">
          <TableHead>
            <TableRow>
              <TableHeader>失败原因</TableHeader>
              <TableHeader>次数</TableHeader>
              <TableHeader>占比</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {reasons.map((item, idx) => {
              const pct = total > 0 ? (item.count / total) * 100 : 0;
              return (
                <TableRow key={idx}>
                  <TableCell className="analytics-failure-reason">{item.reason}</TableCell>
                  <TableCell className="analytics-num">{item.count}</TableCell>
                  <TableCell>
                    <div className="analytics-meter-row">
                      <span className="analytics-meter analytics-meter--red" aria-hidden>
                        <span style={{ width: `${pct}%` }} />
                      </span>
                      <span className="analytics-num">{pct.toFixed(1)}%</span>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** 模块 3：耗时分布 + 阶段排行。 */
function DurationAnalysis({
  buckets,
  phases,
}: {
  buckets: AnalyticsStats['duration_buckets'];
  phases: AnalyticsStats['phase_stats'];
}) {
  const maxCount = Math.max(...buckets.map((b) => b.count), 1);
  return (
    <div className="analytics-two-col">
      <section className="analytics-section">
        <h2 className="panel-title">耗时分布</h2>
        <div className="analytics-table">
          <Table size="sm">
            <TableHead>
              <TableRow>
                <TableHeader>区间</TableHeader>
                <TableHeader>查询数</TableHeader>
                <TableHeader>成功数</TableHeader>
                <TableHeader>平均耗时</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {buckets.map((b) => (
                <TableRow key={b.bucket}>
                  <TableCell>{b.bucket}</TableCell>
                  <TableCell>
                    <div className="analytics-meter-row">
                      <span className="analytics-num">{b.count}</span>
                      <span className="analytics-meter" aria-hidden>
                        <span style={{ width: `${(b.count / maxCount) * 100}%` }} />
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="analytics-num">{b.completed}</TableCell>
                  <TableCell className="analytics-num">{formatDuration(b.avg_ms)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>
      <section className="analytics-section">
        <h2 className="panel-title">阶段耗时排行</h2>
        <p className="analytics-section-hint">
          完成次数表示该阶段产出了结果，不等于整条查询最终成功。
        </p>
        <div className="analytics-table">
          <Table size="sm">
            <TableHead>
              <TableRow>
                <TableHeader>阶段</TableHeader>
                <TableHeader>平均耗时</TableHeader>
                <TableHeader>完成</TableHeader>
                <TableHeader>异常</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {phases.map((p) => (
                <TableRow key={p.step}>
                  <TableCell>{p.step}</TableCell>
                  <TableCell className="analytics-num">{formatDuration(p.avg_ms)}</TableCell>
                  <TableCell className="analytics-num">{p.success_count}</TableCell>
                  <TableCell className="analytics-num">{p.error_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  );
}

/** 模块 4：查询明细表格。 */
function TraceTable({
  traces,
  statusFilter,
  onFilterChange,
  expandedTrace,
  onToggleExpand,
}: {
  traces: TraceDetail[] | null;
  statusFilter: StatusFilter;
  onFilterChange: (s: StatusFilter) => void;
  expandedTrace: string | null;
  onToggleExpand: (id: string) => void;
}) {
  return (
    <section className="analytics-section">
      <div className="analytics-trace-header">
        <h2 className="panel-title">查询明细</h2>
        <div className="analytics-filter" role="group" aria-label="按状态筛选">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={
                statusFilter === f.key ? 'analytics-seg analytics-seg--active' : 'analytics-seg'
              }
              onClick={() => onFilterChange(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      {!traces && <SkeletonText paragraph lineCount={5} />}
      {traces && traces.length === 0 && (
        <div className="analytics-empty">
          <p className="analytics-empty-title">暂无查询记录</p>
          <p className="analytics-empty-hint">{EMPTY_HINTS[statusFilter]}</p>
        </div>
      )}
      {traces && traces.length > 0 && (
        <div className="analytics-table">
          <Table size="sm">
            <TableHead>
              <TableRow>
                <TableHeader className="analytics-chevron-col" aria-label="展开详情" />
                <TableHeader>问题</TableHeader>
                <TableHeader>状态</TableHeader>
                <TableHeader>用户</TableHeader>
                <TableHeader>耗时</TableHeader>
                <TableHeader>时间</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {traces.map((trace) => (
                <TraceRow
                  key={trace.id}
                  trace={trace}
                  expanded={expandedTrace === trace.id}
                  onToggle={() => onToggleExpand(trace.id)}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function TraceRow({
  trace,
  expanded,
  onToggle,
}: {
  trace: TraceDetail;
  expanded: boolean;
  onToggle: () => void;
}) {
  // 后端返回的时间是 UTC（MySQL 容器时区为 UTC），但字符串无时区后缀。
  // 补上 'Z' 让浏览器识别为 UTC，toLocaleString 自动转本地时区显示。
  const rawTime = trace.started_at;
  const utcString = rawTime.endsWith('Z') || rawTime.includes('+') ? rawTime : `${rawTime}Z`;
  const time = new Date(utcString).toLocaleString('zh-CN', { hour12: false });
  const statusView = getTraceStatusView(trace.status);
  const detailMessage = getTraceDetailMessage(trace);
  return (
    <>
      <TableRow onClick={onToggle} className="analytics-trace-row">
        <TableCell className="analytics-chevron">
          <ChevronRight size={14} aria-hidden className={expanded ? 'is-open' : undefined} />
        </TableCell>
        <TableCell className="analytics-trace-question">{trace.query_text}</TableCell>
        <TableCell>
          <Tag type={statusView.type} size="sm">
            {statusView.label}
          </Tag>
        </TableCell>
        <TableCell>{trace.username ?? '-'}</TableCell>
        <TableCell className="analytics-num">
          {trace.total_duration_ms ? formatDuration(trace.total_duration_ms) : '-'}
        </TableCell>
        <TableCell className="analytics-trace-time">{time}</TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="analytics-trace-detail">
          <TableCell colSpan={6}>
            <TraceDetailBlock
              title="完整问题"
              copyLabel="复制完整问题"
              copyValue={trace.query_text}
            >
              <pre className="analytics-question-full">{trace.query_text}</pre>
            </TraceDetailBlock>
            <TraceDetailBlock
              title={trace.status === 'failed' ? '失败 SQL' : '执行 SQL'}
              copyLabel={trace.sql ? '复制 SQL' : undefined}
              copyValue={trace.sql ?? undefined}
            >
              {trace.sql ? (
                <pre
                  className={trace.status === 'failed' ? 'analytics-sql-failed' : 'analytics-sql'}
                >
                  {trace.sql}
                </pre>
              ) : (
                <p className="analytics-detail-empty">
                  {trace.status === 'failed'
                    ? '该历史记录未保存失败 SQL；后续失败查询会记录最后一次 SQL 尝试。'
                    : '该查询没有可展示的 SQL。'}
                </p>
              )}
            </TraceDetailBlock>
            <SqlAttemptHistory trace={trace} />
            <ReferenceSqlHistory trace={trace} />
            {detailMessage && (
              <div className="analytics-detail-block">
                <strong>{trace.status === 'failed' ? '失败原因' : '状态说明'}</strong>
                <pre className={trace.status === 'failed' ? 'analytics-error' : 'analytics-sql'}>
                  {detailMessage}
                </pre>
              </div>
            )}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function ReferenceSqlHistory({ trace }: { trace: TraceDetail }) {
  const references = (trace.phases ?? []).filter(
    (phase) => phase.step === '标准答案SQL' && phase.sql,
  );
  if (references.length === 0) return null;
  return (
    <div className="analytics-detail-block">
      <strong>评测标准 SQL</strong>
      <p className="analytics-detail-empty">用于本次准确率核对的只读标准 SQL。</p>
      {references.map((reference, index) => (
        <div key={`${reference.sequence}-${index}`} className="analytics-attempt">
          <div className="analytics-attempt-heading">
            <span>执行耗时 · {formatDuration(reference.duration_ms)}</span>
            <CopyButton
              feedback="已复制"
              feedbackTimeout={2000}
              iconDescription="复制标准答案 SQL"
              onClick={() => void copyText(reference.sql ?? '')}
            />
          </div>
          <pre className="analytics-sql">{reference.sql}</pre>
        </div>
      ))}
    </div>
  );
}

function SqlAttemptHistory({ trace }: { trace: TraceDetail }) {
  const attempts = (trace.phases ?? []).filter(
    (phase) => phase.step === '校验SQL' && (phase.sql || phase.error_message),
  );
  if (attempts.length === 0) return null;
  return (
    <div className="analytics-detail-block">
      <strong>SQL 校验记录</strong>
      <div className="analytics-attempt-list">
        {attempts.map((attempt, index) => (
          <div key={`${attempt.sequence}-${index}`} className="analytics-attempt">
            <div className="analytics-attempt-heading">
              <span>
                第 {index + 1} 次校验 · {attempt.status === 'success' ? '通过' : '失败'} ·{' '}
                {formatDuration(attempt.duration_ms)}
              </span>
              {attempt.sql && (
                <CopyButton
                  feedback="已复制"
                  feedbackTimeout={2000}
                  iconDescription={`复制第 ${index + 1} 次 SQL`}
                  onClick={() => void copyText(attempt.sql ?? '')}
                />
              )}
            </div>
            {attempt.sql && (
              <pre
                className={attempt.status === 'error' ? 'analytics-sql-failed' : 'analytics-sql'}
              >
                {attempt.sql}
              </pre>
            )}
            {attempt.error_message && (
              <pre className="analytics-error">{attempt.error_message}</pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function TraceDetailBlock({
  title,
  copyLabel,
  copyValue,
  children,
}: {
  title: string;
  copyLabel?: string;
  copyValue?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="analytics-detail-block">
      <div className="analytics-detail-heading">
        <strong>{title}</strong>
        {copyLabel && copyValue && (
          <CopyButton
            feedback="已复制"
            feedbackTimeout={2000}
            iconDescription={copyLabel}
            onClick={() => void copyText(copyValue)}
          />
        )}
      </div>
      {children}
    </div>
  );
}

function getTraceStatusView(status: string): {
  label: string;
  type: 'green' | 'red' | 'blue' | 'gray';
} {
  switch (status) {
    case 'completed':
      return { label: '成功', type: 'green' };
    case 'failed':
      return { label: '失败', type: 'red' };
    case 'cancelled':
      return { label: '已取消', type: 'gray' };
    case 'running':
      return { label: '进行中', type: 'blue' };
    default:
      return { label: '未知', type: 'gray' };
  }
}

function getTraceDetailMessage(trace: TraceDetail): string | null {
  if (trace.error_message) return trace.error_message;
  if (trace.status === 'failed') return '该查询未记录到具体失败原因。';
  if (trace.status === 'cancelled') return '查询已取消。';
  if (trace.status === 'running') {
    return '查询仍在执行，或因页面刷新、连接中断、服务重启而未正常写入终态。';
  }
  return null;
}
