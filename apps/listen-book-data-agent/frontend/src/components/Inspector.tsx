import { Accordion, AccordionItem, CopyButton, Tag } from '@carbon/react';
import { CheckmarkFilled, CircleDash, ErrorFilled, InProgress } from '@carbon/icons-react';
import { copyText } from '../lib/clipboard';
import { formatDuration } from '../lib/format';
import type { QueryState, StepStatus } from '../state/queryReducer';

interface InspectorProps {
  state: QueryState;
}

const INTENT_LABELS: Record<string, string> = {
  ranking: '排名',
  trend: '趋势',
  compare: '对比',
  detail: '明细',
  aggregate: '聚合',
};

const STEP_STATUS_LABELS: Record<StepStatus, string> = {
  running: '进行中',
  success: '完成',
  error: '失败',
  skipped: '已跳过',
  degraded: '已降级',
};

function StepIcon({ status }: { status: StepStatus }) {
  if (status === 'success') {
    return <CheckmarkFilled size={16} className="step-icon step-ok" aria-label="完成" />;
  }
  if (status === 'error') {
    return <ErrorFilled size={16} className="step-icon step-bad" aria-label="失败" />;
  }
  if (status === 'degraded') {
    return <ErrorFilled size={16} className="step-icon step-warn" aria-label="已降级" />;
  }
  if (status === 'running') {
    return <InProgress size={16} className="step-icon step-running" aria-label="进行中" />;
  }
  return <CircleDash size={16} className="step-icon" aria-hidden="true" />;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel inspector-section">
      <h2 className="panel-title">{title}</h2>
      {children}
    </section>
  );
}

function shortId(value: string | null): string {
  if (!value) return '-';
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function planRefs(values: Array<{ semantic_id: string; label: string }>): React.ReactNode {
  return values.map((value) => (
    <span key={value.semantic_id} className="inspector-semantic-ref">
      {value.label} <code>{value.semantic_id}</code>
    </span>
  ));
}

/** Right-hand inspector: plan, metrics, tables, validated SQL, timeline, request id. */
export function Inspector({ state }: InspectorProps) {
  const plan = state.analysisPlan;
  const queryPlan = state.queryPlan;
  const serializedDsl = state.queryDsl ? JSON.stringify(state.queryDsl, null, 2) : null;

  return (
    <div className="inspector-stack">
      <Section title="执行时间线">
        {state.steps.length === 0 ? (
          <p className="muted">执行查询后展示各阶段进度。</p>
        ) : (
          <ol className="timeline">
            {state.steps.map((step) => (
              <li key={step.name} className={`timeline-item timeline-${step.status}`}>
                <StepIcon status={step.status} />
                <span className="timeline-name">{step.name}</span>
                <span className="timeline-status">
                  {STEP_STATUS_LABELS[step.status]}
                  {step.durationMs !== null ? ` · ${formatDuration(step.durationMs)}` : ''}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="QueryPlan">
        {queryPlan ? (
          <dl className="fact-list">
            <div>
              <dt>意图 / 复杂度</dt>
              <dd>
                {INTENT_LABELS[queryPlan.intent] ?? queryPlan.intent} / {queryPlan.complexity}
              </dd>
            </div>
            {queryPlan.metrics.length > 0 && (
              <div>
                <dt>指标</dt>
                <dd className="inspector-semantic-list">{planRefs(queryPlan.metrics)}</dd>
              </div>
            )}
            {queryPlan.dimensions.length > 0 && (
              <div>
                <dt>维度</dt>
                <dd className="inspector-semantic-list">{planRefs(queryPlan.dimensions)}</dd>
              </div>
            )}
            {queryPlan.filters.length > 0 && (
              <div>
                <dt>筛选</dt>
                <dd>
                  {queryPlan.filters.map((filter) => (
                    <span key={filter.filter_id} className="inspector-semantic-ref">
                      {filter.label} <code>{filter.field_ids.join(', ')}</code>
                    </span>
                  ))}
                </dd>
              </div>
            )}
            {(queryPlan.time.label || queryPlan.time.start) && (
              <div>
                <dt>时间</dt>
                <dd>
                  {queryPlan.time.label ?? `${queryPlan.time.start} 至 ${queryPlan.time.end}`}
                  {queryPlan.time.grain ? ` / ${queryPlan.time.grain}` : ''}
                </dd>
              </div>
            )}
            {queryPlan.join_path.length > 0 && (
              <div>
                <dt>Join Path</dt>
                <dd>
                  {queryPlan.join_path.map((value) => (
                    <code key={value}>{value}</code>
                  ))}
                </dd>
              </div>
            )}
            {queryPlan.subplans.length > 0 && (
              <div>
                <dt>子计划</dt>
                <dd>{queryPlan.subplans.map((value) => value.purpose).join('、')}</dd>
              </div>
            )}
            <div>
              <dt>角色 / Dry Plan</dt>
              <dd>
                {state.planningRoles.length > 0 ? state.planningRoles.join(' → ') : '短路径'} /{' '}
                {state.dryPlanStatus === 'validated' ? '已校验' : '待校验'}
              </dd>
            </div>
            {queryPlan.limit !== null && (
              <div>
                <dt>结果上限</dt>
                <dd>{queryPlan.limit}</dd>
              </div>
            )}
          </dl>
        ) : plan ? (
          <dl className="fact-list">
            <div>
              <dt>意图</dt>
              <dd>{INTENT_LABELS[plan.intent] ?? plan.intent}</dd>
            </div>
            {plan.metric_hints.length > 0 && (
              <div>
                <dt>指标提示</dt>
                <dd>{plan.metric_hints.join('、')}</dd>
              </div>
            )}
            {plan.dimensions.length > 0 && (
              <div>
                <dt>维度</dt>
                <dd>{plan.dimensions.join('、')}</dd>
              </div>
            )}
            {plan.filters.length > 0 && (
              <div>
                <dt>筛选</dt>
                <dd>{plan.filters.join('、')}</dd>
              </div>
            )}
            {(plan.time_range.label || plan.time_range.start) && (
              <div>
                <dt>时间范围</dt>
                <dd>
                  {plan.time_range.label ?? ''}
                  {plan.time_range.start && plan.time_range.end
                    ? `（${plan.time_range.start} 至 ${plan.time_range.end}）`
                    : ''}
                </dd>
              </div>
            )}
            {plan.time_grain && (
              <div>
                <dt>时间粒度</dt>
                <dd>{plan.time_grain}</dd>
              </div>
            )}
            {plan.top_n !== null && (
              <div>
                <dt>Top N</dt>
                <dd>{plan.top_n}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="muted">QueryPlan 生成后展示；历史旧记录可能仅保留分析计划。</p>
        )}
      </Section>

      <Section title="语义命中">
        <dl className="fact-list inspector-hits">
          <div>
            <dt>数据表</dt>
            <dd>
              {state.tables.length > 0
                ? state.tables.map((table) => <code key={table}>{table}</code>)
                : '无命中'}
            </dd>
          </div>
          <div>
            <dt>术语</dt>
            <dd>
              {state.semanticTermMatches.length > 0
                ? state.semanticTermMatches.map((term) => (
                    <span key={`${term.term_key}-${term.version}`}>
                      {term.standard_term} v{term.version}
                    </span>
                  ))
                : '无命中'}
            </dd>
          </div>
          <div>
            <dt>可信案例</dt>
            <dd>
              {state.verifiedQueryMatch ? (
                <span>精确：{state.verifiedQueryMatch.case_key}</span>
              ) : state.verifiedQueryExamples.length > 0 ? (
                state.verifiedQueryExamples.map((item) => (
                  <span key={item.revision_id ?? item.case_key}>近似：{item.case_key}</span>
                ))
              ) : (
                '无命中'
              )}
            </dd>
          </div>
          <div>
            <dt>业务规则</dt>
            <dd>
              {state.businessRuleMatches.length > 0
                ? state.businessRuleMatches.map((rule) => (
                    <span key={`${rule.rule_key}-${rule.version}`}>
                      {rule.rule_key} v{rule.version}
                    </span>
                  ))
                : '无命中'}
            </dd>
          </div>
        </dl>
      </Section>

      <Section title="生效版本">
        <dl className="fact-list">
          <div>
            <dt>Schema Build</dt>
            <dd>
              <code>{shortId(state.buildId)}</code>
            </dd>
          </div>
          <div>
            <dt>Semantic Release</dt>
            <dd>
              {state.semanticReleaseVersion !== null ? `v${state.semanticReleaseVersion} · ` : ''}
              <code>{shortId(state.semanticReleaseId)}</code>
            </dd>
          </div>
          <div>
            <dt>Query Set</dt>
            <dd>
              {state.querySetVersion !== null ? `v${state.querySetVersion} · ` : ''}
              <code>{shortId(state.querySetId)}</code>
            </dd>
          </div>
          <div>
            <dt>Rule Set</dt>
            <dd>
              {state.businessRuleSetVersion !== null ? `v${state.businessRuleSetVersion} · ` : ''}
              <code>{shortId(state.businessRuleSetId)}</code>
            </dd>
          </div>
          <div>
            <dt>Policy</dt>
            <dd>
              {state.policyVersion ?? '-'} · <code>{shortId(state.policyHash)}</code>
              {state.policyAdminBypass ? '（管理员显式绕过）' : ''}
            </dd>
          </div>
        </dl>
      </Section>

      <Section title="生成链路">
        {state.generationSource ? (
          <dl className="fact-list">
            <div>
              <dt>模式</dt>
              <dd>{state.generationMode === 'dsl' ? 'DSL 实验链路' : 'Legacy SQL 链路'}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>
                <code>{state.generationSource}</code>
              </dd>
            </div>
            <div>
              <dt>LLM 调用</dt>
              <dd>{state.llmCalls} 次</dd>
            </div>
            {state.generationMode === 'dsl' && (
              <div>
                <dt>DSL 尝试</dt>
                <dd>{state.dslAttempts} 次</dd>
              </div>
            )}
            {state.sqlCorrectionAttempts > 0 && (
              <div>
                <dt>SQL 修复</dt>
                <dd>{state.sqlCorrectionAttempts} 次</dd>
              </div>
            )}
            {state.dslFallbackReason && (
              <div>
                <dt>回退原因</dt>
                <dd>{state.dslFallbackReason}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="muted">生成阶段完成后展示执行链路。</p>
        )}
      </Section>

      {serializedDsl && (
        <Section title="QueryDSL">
          <div className="sql-wrap">
            <pre className="sql-code">{serializedDsl}</pre>
            <CopyButton
              className="sql-copy"
              feedback="已复制"
              feedbackTimeout={2000}
              iconDescription="复制 QueryDSL"
              onClick={() => void copyText(serializedDsl)}
            />
          </div>
        </Section>
      )}

      <Section title="已校验 SQL">
        {state.sql ? (
          <Accordion size="sm">
            <AccordionItem
              title={
                <span className="sql-accordion-title">
                  查看 SQL
                  <Tag size="sm" type="teal">
                    {state.sqlStatus === 'validated' ? '已校验' : (state.sqlStatus ?? '已校验')}
                  </Tag>
                </span>
              }
            >
              <div className="sql-wrap">
                <pre className="sql-code">{state.sql}</pre>
                <CopyButton
                  className="sql-copy"
                  feedback="已复制"
                  feedbackTimeout={2000}
                  iconDescription="复制 SQL"
                  onClick={() => void copyText(state.sql ?? '')}
                />
              </div>
            </AccordionItem>
          </Accordion>
        ) : (
          <p className="muted">SQL 通过安全校验后展示，只读不可编辑。</p>
        )}
      </Section>

      <Section title="请求信息">
        <dl className="fact-list">
          <div>
            <dt>请求 ID</dt>
            <dd className="request-id">
              {state.requestId ? (
                <>
                  <code>{state.requestId}</code>
                  <CopyButton
                    feedback="已复制"
                    feedbackTimeout={2000}
                    iconDescription="复制请求 ID"
                    onClick={() => void copyText(state.requestId ?? '')}
                  />
                </>
              ) : (
                '-'
              )}
            </dd>
          </div>
          <div>
            <dt>耗时</dt>
            <dd>{state.durationMs !== null ? formatDuration(state.durationMs) : '-'}</dd>
          </div>
          <div>
            <dt>LLM / Token</dt>
            <dd>
              {state.llmCalls} 次 /{' '}
              {state.tokenUsage?.available
                ? `${state.tokenUsage.total_tokens}（输入 ${state.tokenUsage.input_tokens}，输出 ${state.tokenUsage.output_tokens}）`
                : '供应商未返回 token 统计'}
            </dd>
          </div>
        </dl>
      </Section>
    </div>
  );
}
