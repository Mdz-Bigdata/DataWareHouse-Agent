import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, InlineNotification, Tag, TextInput } from '@carbon/react';
import { Renew, Search } from '@carbon/icons-react';
import type { RecallTestResult, RebuildStatus } from '../lib/semantic';
import { fetchRebuildStatus, recallTest, startRebuild } from '../lib/semantic';

const STATUS_LABELS: Record<RebuildStatus['status'], string> = {
  idle: '空闲',
  running: '重建中',
  completed: '完成',
  failed: '失败',
};

const STATUS_TAG_TYPES: Record<RebuildStatus['status'], 'green' | 'blue' | 'red' | 'cool-gray'> = {
  idle: 'cool-gray',
  running: 'blue',
  completed: 'green',
  failed: 'red',
};

/** 知识库重建与召回测试面板（M3c）。 */
export function RebuildRecallPanel() {
  const [status, setStatus] = useState<RebuildStatus | null>(null);
  const [feedback, setFeedback] = useState<{ kind: 'success' | 'error'; text: string } | null>(
    null,
  );
  const [question, setQuestion] = useState('');
  const [recallResult, setRecallResult] = useState<RecallTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollStatus = useCallback(() => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const next = await fetchRebuildStatus();
        setStatus(next);
        if (next.status !== 'running') stopPolling();
      } catch {
        stopPolling();
      }
    }, 3000);
  }, []);

  useEffect(() => {
    void fetchRebuildStatus()
      .then((next) => {
        setStatus(next);
        if (next.status === 'running') pollStatus();
      })
      .catch(() => undefined);
    return stopPolling;
  }, [pollStatus]);

  const rebuild = async () => {
    setFeedback(null);
    try {
      await startRebuild();
      setStatus({ status: 'running', error: null, started_at: null, finished_at: null });
      setFeedback({ kind: 'success', text: '重建任务已启动，索引切换后下一次查询生效。' });
      pollStatus();
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : '启动重建失败',
      });
    }
  };

  const runRecallTest = async () => {
    const trimmed = question.trim();
    if (!trimmed || testing) return;
    setTesting(true);
    setFeedback(null);
    try {
      setRecallResult(await recallTest(trimmed));
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : '召回测试失败',
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <section className="panel admin-panel-wide" aria-labelledby="rebuild-title">
      <h2 className="panel-title" id="rebuild-title">
        知识库与召回测试
      </h2>

      {feedback && (
        <InlineNotification
          kind={feedback.kind}
          lowContrast
          title={feedback.text}
          onCloseButtonClick={() => setFeedback(null)}
          className="admin-feedback"
        />
      )}

      <div className="rebuild-row">
        <Button kind="primary" size="sm" renderIcon={Renew} onClick={() => void rebuild()}>
          重建知识库
        </Button>
        {status && (
          <span className="rebuild-status">
            <Tag type={STATUS_TAG_TYPES[status.status]} size="sm">
              {STATUS_LABELS[status.status]}
            </Tag>
            {status.started_at && <span className="muted">开始 {status.started_at}</span>}
            {status.finished_at && <span className="muted">结束 {status.finished_at}</span>}
            {status.error && <span className="rebuild-error">{status.error}</span>}
          </span>
        )}
      </div>
      <p className="muted rebuild-note">
        以当前元数据为源重建字段/指标向量与枚举值索引，alias 原子切换，无需重启。
      </p>

      <div className="recall-test">
        <div className="recall-input-row">
          <TextInput
            id="recall-question"
            labelText="召回测试：输入问题，查看命中了哪些表、字段和指标"
            placeholder="例如：热搜榜月榜有哪些搜索词"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void runRecallTest();
            }}
          />
          <Button
            kind="secondary"
            size="md"
            renderIcon={Search}
            disabled={testing || question.trim() === ''}
            onClick={() => void runRecallTest()}
          >
            {testing ? '测试中…' : '测试召回'}
          </Button>
        </div>

        {recallResult && (
          <div className="recall-result">
            {recallResult.warnings.map((warning) => (
              <InlineNotification
                key={warning}
                kind="warning"
                lowContrast
                hideCloseButton
                title={warning}
              />
            ))}
            <div className="recall-block">
              <h3 className="recall-block-title">关键词</h3>
              <div className="recall-tags">
                {recallResult.keywords.map((keyword) => (
                  <Tag key={keyword} type="cool-gray" size="sm">
                    {keyword}
                  </Tag>
                ))}
              </div>
            </div>
            <div className="recall-block">
              <h3 className="recall-block-title">命中数据表（{recallResult.tables.length}）</h3>
              <div className="recall-tags">
                {recallResult.tables.map((table) => (
                  <Tag key={table} type="green" size="sm">
                    {table}
                  </Tag>
                ))}
                {recallResult.tables.length === 0 && <span className="muted">无</span>}
              </div>
            </div>
            <div className="recall-block">
              <h3 className="recall-block-title">命中字段（{recallResult.columns.length}）</h3>
              <ul className="recall-list">
                {recallResult.columns.map((column) => (
                  <li key={column.id}>
                    <code>{column.id}</code>
                    <span className="muted">{column.description}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="recall-block">
              <h3 className="recall-block-title">命中指标（{recallResult.metrics.length}）</h3>
              <ul className="recall-list">
                {recallResult.metrics.map((metric) => (
                  <li key={metric.id}>
                    <code>{metric.id}</code>
                    <span>{metric.description}</span>
                    <code className="recall-formula">{metric.formula}</code>
                  </li>
                ))}
                {recallResult.metrics.length === 0 && <li className="muted">无</li>}
              </ul>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
