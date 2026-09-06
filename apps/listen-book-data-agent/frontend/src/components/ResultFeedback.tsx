import { useEffect, useState } from 'react';
import { Button, InlineNotification, TextArea } from '@carbon/react';
import { ThumbsDown, ThumbsUp } from '@carbon/icons-react';
import { submitTraceFeedback, type FeedbackVerdict } from '../lib/feedback';

const REASONS: Record<FeedbackVerdict, Array<{ value: string; label: string }>> = {
  correct: [
    { value: 'accurate', label: '结果准确' },
    { value: 'clear', label: '解释清晰' },
    { value: 'helpful', label: '对分析有帮助' },
    { value: 'other', label: '其他' },
  ],
  incorrect: [
    { value: 'wrong_metric', label: '指标口径错误' },
    { value: 'wrong_filter', label: '筛选条件错误' },
    { value: 'wrong_join', label: '表关联错误' },
    { value: 'wrong_time_range', label: '时间范围错误' },
    { value: 'wrong_granularity', label: '统计粒度错误' },
    { value: 'missing_data', label: '结果缺少数据' },
    { value: 'other', label: '其他' },
  ],
};

export function ResultFeedback({ requestId }: { requestId: string }) {
  const [verdict, setVerdict] = useState<FeedbackVerdict | null>(null);
  const [reason, setReason] = useState('');
  const [comment, setComment] = useState('');
  const [status, setStatus] = useState<'idle' | 'submitting' | 'submitted'>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setVerdict(null);
    setReason('');
    setComment('');
    setStatus('idle');
    setError(null);
  }, [requestId]);

  const choose = (value: FeedbackVerdict) => {
    setVerdict(value);
    setReason(REASONS[value][0].value);
    setError(null);
  };

  const submit = async () => {
    if (!verdict || !reason) return;
    setStatus('submitting');
    setError(null);
    try {
      await submitTraceFeedback(requestId, {
        verdict,
        reasons: [reason],
        comment: comment.trim(),
      });
      setStatus('submitted');
    } catch (submissionError) {
      setStatus('idle');
      setError(
        submissionError instanceof Error ? submissionError.message : '反馈提交失败，请稍后重试。',
      );
    }
  };

  if (status === 'submitted') {
    return (
      <InlineNotification
        kind="success"
        lowContrast
        hideCloseButton
        title="反馈已提交"
        subtitle={
          verdict === 'incorrect' ? '已生成待人工审核的候选案例。' : '已累计本次结果可信度。'
        }
      />
    );
  }

  return (
    <section className="panel result-feedback" aria-label="结果反馈">
      <div className="result-feedback__heading">
        <div>
          <h2 className="panel-title">这个结果有帮助吗？</h2>
          <p className="muted">反馈仅用于案例治理，不会保存查询结果行。</p>
        </div>
        <div className="result-feedback__actions">
          <Button
            kind={verdict === 'correct' ? 'primary' : 'tertiary'}
            size="sm"
            renderIcon={ThumbsUp}
            onClick={() => choose('correct')}
          >
            结果正确
          </Button>
          <Button
            kind={verdict === 'incorrect' ? 'danger' : 'tertiary'}
            size="sm"
            renderIcon={ThumbsDown}
            onClick={() => choose('incorrect')}
          >
            结果有误
          </Button>
        </div>
      </div>

      {verdict && (
        <div className="result-feedback__form">
          <label className="result-feedback__label" htmlFor="feedback-reason">
            原因
          </label>
          <select
            id="feedback-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          >
            {REASONS[verdict].map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <TextArea
            id="feedback-comment"
            labelText="补充说明（可选）"
            value={comment}
            maxCount={1000}
            enableCounter
            onChange={(event) => setComment(event.target.value)}
          />
          {error && (
            <InlineNotification
              kind="error"
              lowContrast
              hideCloseButton
              title="提交失败"
              subtitle={error}
            />
          )}
          <Button size="sm" disabled={status === 'submitting'} onClick={() => void submit()}>
            {status === 'submitting' ? '提交中…' : '提交反馈'}
          </Button>
        </div>
      )}
    </section>
  );
}
