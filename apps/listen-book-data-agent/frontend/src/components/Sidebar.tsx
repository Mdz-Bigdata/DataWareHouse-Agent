import { useMemo, useState } from 'react';
import { Accordion, AccordionItem, Button, Search, Tag } from '@carbon/react';
import { Add, Archive, CheckmarkFilled, Edit, ErrorFilled, Renew, Time } from '@carbon/icons-react';
import { SAMPLE_QUESTIONS } from '../data/sampleQuestions';
import type { Conversation } from '../lib/conversations';
import { formatClock, formatDuration } from '../lib/format';
import type { HistoryEntry } from '../state/historyReducer';
import type { InsightCard } from '../lib/insightCards';
import { InsightCardsPanel } from './InsightCardsPanel';

interface SidebarProps {
  isActive: boolean;
  conversations: Conversation[];
  activeConversationId: string | null;
  history: HistoryEntry[];
  onNew: () => void;
  onRunQuestion: (question: string) => void;
  onSelectConversation: (conversationId: string) => void;
  onRenameConversation: (conversationId: string, title: string) => void;
  onArchiveConversation: (conversationId: string) => void;
  onRestoreTurn: (entry: HistoryEntry) => void;
  onRegenerate: (entry: HistoryEntry) => void;
  insightRefreshToken: number;
  onOpenInsight: (card: InsightCard) => void;
}

const STATUS_LABELS: Record<HistoryEntry['status'], string> = {
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  needs_input: '待补充',
};

function StatusDot({ status }: { status: HistoryEntry['status'] }) {
  if (status === 'completed') {
    return <CheckmarkFilled size={14} className="history-icon history-ok" aria-label="已完成" />;
  }
  if (status === 'failed') {
    return <ErrorFilled size={14} className="history-icon history-bad" aria-label="失败" />;
  }
  return <Time size={14} className="history-icon" aria-label={STATUS_LABELS[status]} />;
}

export function Sidebar({
  isActive,
  conversations,
  activeConversationId,
  history,
  onNew,
  onRunQuestion,
  onSelectConversation,
  onRenameConversation,
  onArchiveConversation,
  onRestoreTurn,
  onRegenerate,
  insightRefreshToken,
  onOpenInsight,
}: SidebarProps) {
  const [search, setSearch] = useState('');
  const visibleConversations = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) return conversations;
    return conversations.filter((conversation) =>
      conversation.title.toLowerCase().includes(normalized),
    );
  }, [conversations, search]);

  return (
    <div className="sidebar-stack">
      <Button kind="primary" size="md" renderIcon={Add} onClick={onNew} className="new-analysis">
        新建分析
      </Button>

      <section className="panel conversation-panel" aria-labelledby="conversations-title">
        <h2 id="conversations-title" className="panel-title">
          会话
        </h2>
        <Search
          size="sm"
          labelText="搜索会话"
          placeholder="搜索会话"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        {visibleConversations.length === 0 ? (
          <p className="muted conversation-empty">暂无匹配会话。</p>
        ) : (
          <ul className="conversation-list">
            {visibleConversations.map((conversation) => (
              <li
                key={conversation.id}
                className={conversation.id === activeConversationId ? 'conversation-active' : ''}
              >
                <button
                  type="button"
                  className="conversation-select"
                  disabled={isActive}
                  aria-current={conversation.id === activeConversationId ? 'true' : undefined}
                  onClick={() => onSelectConversation(conversation.id)}
                >
                  {conversation.title}
                </button>
                <button
                  type="button"
                  className="conversation-action"
                  aria-label={`重命名会话：${conversation.title}`}
                  onClick={() => {
                    const title = window.prompt('请输入新的会话标题', conversation.title);
                    if (title) onRenameConversation(conversation.id, title);
                  }}
                >
                  <Edit size={14} />
                </button>
                <button
                  type="button"
                  className="conversation-action"
                  aria-label={`归档会话：${conversation.title}`}
                  onClick={() => onArchiveConversation(conversation.id)}
                >
                  <Archive size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel" aria-labelledby="samples-title">
        <h2 id="samples-title" className="panel-title">
          示例问题
        </h2>
        <Accordion size="sm">
          {SAMPLE_QUESTIONS.map((group) => (
            <AccordionItem key={group.category} title={group.category}>
              <ul className="sample-list">
                {group.questions.map((question) => (
                  <li key={question}>
                    <button
                      type="button"
                      className="sample-question"
                      disabled={isActive}
                      onClick={() => onRunQuestion(question)}
                    >
                      {question}
                    </button>
                  </li>
                ))}
              </ul>
            </AccordionItem>
          ))}
        </Accordion>
      </section>

      <InsightCardsPanel
        isActive={isActive}
        refreshToken={insightRefreshToken}
        onOpen={onOpenInsight}
      />

      <section className="panel history-panel" aria-labelledby="history-title">
        <h2 id="history-title" className="panel-title">
          当前会话轮次
        </h2>
        {history.length === 0 ? (
          <p className="muted">当前会话还没有查询轮次。</p>
        ) : (
          <ul className="history-list">
            {history.map((entry) => (
              <li key={entry.id} className="history-item">
                <button
                  type="button"
                  className="history-entry"
                  disabled={isActive}
                  onClick={() => onRestoreTurn(entry)}
                  aria-label={`打开历史：${entry.question}（${STATUS_LABELS[entry.status]}）`}
                >
                  <span className="history-line">
                    <StatusDot status={entry.status} />
                    <span className="history-question">{entry.question}</span>
                  </span>
                  <span className="history-meta">
                    {formatClock(entry.startedAt)}
                    {entry.rowCount !== null && ` · ${entry.rowCount} 行`}
                    {entry.durationMs !== null && ` · ${formatDuration(entry.durationMs)}`}
                  </span>
                </button>
                {entry.requestId && entry.status !== 'running' && (
                  <button
                    type="button"
                    className="history-regenerate"
                    disabled={isActive}
                    aria-label={`重生成：${entry.question}`}
                    onClick={() => onRegenerate(entry)}
                  >
                    <Renew size={14} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className="muted sidebar-note">
        <Tag size="sm" type="cool-gray">
          提示
        </Tag>{' '}
        会话和摘要按账号保存；结果行不会落库。
      </div>
    </div>
  );
}
