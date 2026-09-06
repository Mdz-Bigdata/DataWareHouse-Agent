import { useEffect, useState } from 'react';
import { Button, InlineNotification } from '@carbon/react';
import { Bookmark } from '@carbon/icons-react';
import { saveInsightCard } from '../lib/insightCards';

interface InsightCardSaveProps {
  requestId: string;
  onSaved: () => void;
}

export function InsightCardSave({ requestId, onSaved }: InsightCardSaveProps) {
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [error, setError] = useState('');

  useEffect(() => {
    setStatus('idle');
    setError('');
  }, [requestId]);

  const save = async () => {
    setStatus('saving');
    setError('');
    try {
      await saveInsightCard(requestId);
      setStatus('saved');
      onSaved();
    } catch (reason) {
      setStatus('error');
      setError(reason instanceof Error ? reason.message : '保存洞察卡片失败。');
    }
  };

  return (
    <section className="panel insight-save" aria-labelledby="insight-save-title">
      <div className="insight-save__heading">
        <div>
          <h2 id="insight-save-title" className="panel-title">
            保存洞察
          </h2>
          <p className="muted">只保存问题、答案、参数化 SQL、图表配置和版本信息，不保存结果行。</p>
        </div>
        <Button
          kind="tertiary"
          size="sm"
          renderIcon={Bookmark}
          disabled={status === 'saving' || status === 'saved'}
          onClick={() => void save()}
        >
          {status === 'saving' ? '保存中…' : status === 'saved' ? '已保存' : '保存洞察卡片'}
        </Button>
      </div>
      {status === 'saved' && (
        <InlineNotification
          kind="success"
          lowContrast
          hideCloseButton
          title="洞察卡片已保存"
          subtitle="可从左侧重新鉴权打开。"
        />
      )}
      {status === 'error' && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title="保存失败"
          subtitle={error}
        />
      )}
    </section>
  );
}
