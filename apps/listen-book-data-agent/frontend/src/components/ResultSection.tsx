import { Button, InlineNotification, SkeletonPlaceholder, SkeletonText, Tag } from '@carbon/react';
import { ChartLine, Renew, Search } from '@carbon/icons-react';
import { formatDuration } from '../lib/format';
import type { QueryState } from '../state/queryReducer';
import { DeepAnalysisPanel } from './DeepAnalysisPanel';
import { ResultFeedback } from './ResultFeedback';
import { ResultVisualization } from './ResultVisualization';
import { InsightCardSave } from './InsightCardSave';

interface ResultSectionProps {
  state: QueryState;
  theme: 'g10' | 'g100';
  onRetry: () => void;
  onRunRecommendation: (question: string) => void;
  onInsightSaved: () => void;
}

function liveStatusText(state: QueryState): string {
  switch (state.phase) {
    case 'idle':
      return '';
    case 'connecting':
      return '正在连接查询服务…';
    case 'streaming': {
      const done = state.steps.filter((step) => step.status === 'success').length;
      return `正在执行查询，已完成 ${done} 个阶段。`;
    }
    case 'completed':
      return `查询完成，返回 ${state.rowCount} 行，耗时 ${formatDuration(state.durationMs)}。`;
    case 'failed':
      return `查询失败。${state.error ?? ''}`;
    case 'cancelled':
      return '查询已取消。';
    case 'needs_input':
      return `需要补充查询条件。${state.clarification ?? ''}`;
    case 'restored':
      return '已恢复历史轮次摘要。';
  }
}

/** 加载态的可见状态行：当前进行中的阶段 + 已完成阶段数。 */
function loadingText(state: QueryState): string {
  if (state.phase === 'connecting') return '正在连接查询服务…';
  const running = state.steps.find((step) => step.status === 'running');
  const done = state.steps.filter((step) => step.status === 'success').length;
  if (running) {
    return done > 0 ? `正在${running.name}…（已完成 ${done} 个阶段）` : `正在${running.name}…`;
  }
  return '正在执行查询…';
}

export function ResultSection({
  state,
  theme,
  onRetry,
  onRunRecommendation,
  onInsightSaved,
}: ResultSectionProps) {
  const active = state.phase === 'connecting' || state.phase === 'streaming';
  return (
    <section className="results stack" aria-label="查询结果">
      <div role="status" aria-live="polite" className="visually-hidden">
        {liveStatusText(state)}
      </div>

      {state.phase === 'idle' && (
        <div className="panel empty-state">
          <div className="empty-icon">
            <ChartLine size={28} aria-hidden="true" />
          </div>
          <p>输入问题，或从左侧选择示例问题开始分析。</p>
          <p className="muted">
            结果支持指标卡、趋势图、对比图和数据表格，SQL 与分析过程在右侧检查器中查看。
          </p>
        </div>
      )}

      {state.phase === 'failed' && (
        <div className="banner-row">
          <InlineNotification
            kind="error"
            lowContrast
            title="查询失败"
            subtitle={state.error ?? '发生未知错误。'}
            hideCloseButton
          />
          <Button kind="tertiary" size="sm" renderIcon={Renew} onClick={onRetry}>
            重试
          </Button>
        </div>
      )}

      {state.phase === 'cancelled' && (
        <div className="banner-row">
          <InlineNotification
            kind="info"
            lowContrast
            title="查询已取消"
            subtitle={
              state.rows.length > 0 ? '以下为取消前已接收的部分数据。' : '未接收任何结果数据。'
            }
            hideCloseButton
          />
          <Button kind="tertiary" size="sm" renderIcon={Renew} onClick={onRetry}>
            重试
          </Button>
        </div>
      )}

      {state.phase === 'needs_input' && (
        <InlineNotification
          kind="info"
          lowContrast
          title="需要补充条件"
          subtitle={state.clarification ?? '请补充指标、筛选、时间范围或粒度后继续。'}
          hideCloseButton
        />
      )}

      {state.phase === 'restored' && (
        <InlineNotification
          kind="info"
          lowContrast
          title="已恢复历史轮次"
          subtitle="已加载问题、答案摘要、计划和 SQL；结果行按安全策略不持久化。"
          hideCloseButton
        />
      )}

      {state.truncated && (
        <InlineNotification
          kind="warning"
          lowContrast
          title="结果已截断"
          subtitle="最多返回 500 行，请增加筛选条件缩小范围后重试。"
          hideCloseButton
        />
      )}

      {state.warnings.map((warning) => (
        <InlineNotification
          key={warning}
          kind="warning"
          lowContrast
          title="召回提示"
          subtitle={warning}
          hideCloseButton
        />
      ))}

      {active && state.rows.length === 0 && (
        <div className="panel skeleton-panel" aria-label="查询进行中">
          <div className="skeleton-status">
            <span className="skeleton-pulse" aria-hidden />
            <span>{loadingText(state)}</span>
          </div>
          <SkeletonText heading width="30%" />
          <SkeletonText paragraph lineCount={2} />
          <div className="skeleton-metrics" aria-hidden>
            <SkeletonPlaceholder />
            <SkeletonPlaceholder />
            <SkeletonPlaceholder />
          </div>
          <SkeletonPlaceholder className="skeleton-block" />
          <div className="skeleton-table" aria-hidden>
            <SkeletonText paragraph lineCount={4} />
          </div>
        </div>
      )}

      {(state.answer || (active && state.rows.length > 0)) && (
        <section className="panel answer-card" aria-labelledby="answer-title">
          <h2 id="answer-title" className="panel-title">
            结果解释
          </h2>
          {state.answer ? (
            <>
              <p className="answer-text">{state.answer.summary}</p>
              <div className="answer-meta">
                {state.answer.metrics.map((metric) => (
                  <Tag key={metric} type="green" size="sm">
                    {metric}
                  </Tag>
                ))}
                <span className="muted-inline">时间范围：{state.answer.timeRange}</span>
              </div>
            </>
          ) : (
            <SkeletonText paragraph lineCount={2} aria-label="解释生成中" />
          )}
        </section>
      )}

      {state.rows.length > 0 && (
        <ResultVisualization
          chartSpec={state.chartSpec}
          columns={state.columns}
          rows={state.rows}
          truncated={state.truncated}
          requestId={state.requestId}
          theme={theme}
        />
      )}

      {state.phase === 'completed' && state.rowCount === 0 && (
        <div className="panel empty-state">
          <div className="empty-icon">
            <Search size={26} aria-hidden="true" />
          </div>
          <p>查询未返回数据。</p>
          <p className="muted">可以尝试调整时间范围或筛选条件后重新查询。</p>
        </div>
      )}

      {state.phase === 'completed' && state.requestId && (
        <>
          <InsightCardSave requestId={state.requestId} onSaved={onInsightSaved} />
          <DeepAnalysisPanel requestId={state.requestId} />
          <ResultFeedback requestId={state.requestId} />
        </>
      )}

      {state.recommendations.length > 0 && (
        <section className="panel recommendations" aria-labelledby="recommendations-title">
          <h2 id="recommendations-title" className="panel-title">
            继续追问
          </h2>
          <div className="recommendation-list">
            {state.recommendations.map((question) => (
              <Button
                key={question}
                kind="tertiary"
                size="sm"
                disabled={active}
                onClick={() => onRunRecommendation(question)}
              >
                {question}
              </Button>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
