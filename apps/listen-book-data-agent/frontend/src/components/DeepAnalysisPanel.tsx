import { useEffect, useState } from 'react';
import { Button, InlineLoading, InlineNotification, Tag } from '@carbon/react';
import {
  requestDeepAnalysis,
  type DeepAnalysisEvidence,
  type DeepAnalysisResult,
} from '../lib/deepAnalysis';

function evidenceText(evidence: DeepAnalysisEvidence): string {
  return Object.entries(evidence.values)
    .map(([key, value]) => `${key}=${value ?? '空'}`)
    .join('，');
}

export function DeepAnalysisPanel({ requestId }: { requestId: string }) {
  const [status, setStatus] = useState<'idle' | 'loading' | 'completed'>('idle');
  const [result, setResult] = useState<DeepAnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus('idle');
    setResult(null);
    setError(null);
  }, [requestId]);

  const analyze = async () => {
    setStatus('loading');
    setError(null);
    try {
      setResult(await requestDeepAnalysis(requestId));
      setStatus('completed');
    } catch (analysisError) {
      setStatus('idle');
      setError(
        analysisError instanceof Error ? analysisError.message : '深入分析失败，请稍后重试。',
      );
    }
  };

  return (
    <section className="panel deep-analysis" aria-label="深入分析">
      <div className="deep-analysis__heading">
        <div>
          <h2 className="panel-title">深入分析</h2>
          <p className="muted">按当前权限重新校验并限量执行原查询，分析结果不会保存数据行。</p>
        </div>
        {status === 'loading' ? (
          <InlineLoading description="正在重新鉴权并分析…" />
        ) : (
          <Button kind="tertiary" size="sm" onClick={() => void analyze()}>
            {result ? '重新分析' : '深入分析'}
          </Button>
        )}
      </div>

      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title="深入分析失败"
          subtitle={error}
        />
      )}

      {result && (
        <div className="deep-analysis__result">
          <div className="deep-analysis__meta">
            <Tag type="green" size="sm">
              当前权限 {result.policy_version}
            </Tag>
            <Tag type="cool-gray" size="sm">
              语义构建 {result.build_id}
            </Tag>
            <span className="muted-inline">
              重跑 {result.rerun_row_count} 行 / 上限 {result.row_limit} 行
              {result.truncated ? '（已截断）' : ''}
            </span>
          </div>

          <div className="deep-analysis__columns">
            <section aria-labelledby="deep-analysis-facts">
              <h3 id="deep-analysis-facts">事实</h3>
              <ul>
                {result.facts.map((fact) => (
                  <li key={fact.fact_id}>{fact.statement}</li>
                ))}
              </ul>
            </section>
            <section aria-labelledby="deep-analysis-inferences">
              <h3 id="deep-analysis-inferences">推断</h3>
              {result.inferences.length > 0 ? (
                <ul>
                  {result.inferences.map((inference) => (
                    <li key={inference.inference_id}>
                      {inference.statement}
                      <Tag type="blue" size="sm">
                        置信度 {inference.confidence}
                      </Tag>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">本次有限结果没有足够证据支持额外推断。</p>
              )}
            </section>
          </div>

          <details className="deep-analysis__evidence">
            <summary>查看具体证据（{result.evidence.length} 项）</summary>
            <dl>
              {result.evidence.map((evidence) => (
                <div key={evidence.evidence_id}>
                  <dt>{evidence.description}</dt>
                  <dd>{evidenceText(evidence)}</dd>
                </div>
              ))}
            </dl>
          </details>
          <p className="muted deep-analysis__disclaimer">{result.disclaimer}</p>
          <p className="visually-hidden">分析子追踪：{result.trace_id}</p>
        </div>
      )}
    </section>
  );
}
