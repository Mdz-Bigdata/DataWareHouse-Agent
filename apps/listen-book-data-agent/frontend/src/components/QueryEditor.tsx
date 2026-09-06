import { useState, type KeyboardEvent, type RefObject } from 'react';
import { Button, TextArea } from '@carbon/react';
import { Play, Renew, Stop } from '@carbon/icons-react';
import type { QueryPhase } from '../state/queryReducer';

interface QueryEditorProps {
  question: string;
  phase: QueryPhase;
  isActive: boolean;
  inputRef: RefObject<HTMLTextAreaElement>;
  onQuestionChange: (value: string) => void;
  onRun: () => void;
  onCancel: () => void;
  onRetry: () => void;
}

export function QueryEditor({
  question,
  phase,
  isActive,
  inputRef,
  onQuestionChange,
  onRun,
  onCancel,
  onRetry,
}: QueryEditorProps) {
  const [showEmptyError, setShowEmptyError] = useState(false);
  const trimmed = question.trim();

  const submit = () => {
    if (!trimmed) {
      setShowEmptyError(true);
      inputRef.current?.focus();
      return;
    }
    setShowEmptyError(false);
    onRun();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      if (!isActive) submit();
    }
  };

  return (
    <section className="panel query-panel" aria-labelledby="query-label">
      <TextArea
        id="query-input"
        ref={inputRef}
        labelText="自然语言问题"
        placeholder="例如：最近7天播放量最高的前10个专辑"
        helperText="Ctrl/⌘ + Enter 执行，问题不超过 500 字。"
        value={question}
        maxLength={500}
        rows={3}
        invalid={showEmptyError}
        invalidText="请输入问题后再执行查询。"
        onChange={(event) => {
          onQuestionChange(event.target.value);
          if (showEmptyError) setShowEmptyError(false);
        }}
        onKeyDown={onKeyDown}
      />
      <div className="query-actions">
        {isActive ? (
          <>
            <Button kind="primary" size="md" disabled>
              查询中…
            </Button>
            <Button kind="danger--tertiary" size="md" renderIcon={Stop} onClick={onCancel}>
              取消查询
            </Button>
          </>
        ) : (
          <>
            <Button kind="primary" size="md" renderIcon={Play} disabled={!trimmed} onClick={submit}>
              执行查询
            </Button>
            {(phase === 'failed' || phase === 'cancelled') && (
              <Button kind="tertiary" size="md" renderIcon={Renew} onClick={onRetry}>
                重试
              </Button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
