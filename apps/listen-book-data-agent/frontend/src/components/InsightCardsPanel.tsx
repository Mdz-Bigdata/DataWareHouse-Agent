import { useEffect, useState } from 'react';
import { Button, InlineNotification } from '@carbon/react';
import { Renew, TrashCan } from '@carbon/icons-react';
import { deleteInsightCard, fetchInsightCards, type InsightCard } from '../lib/insightCards';

interface InsightCardsPanelProps {
  isActive: boolean;
  refreshToken: number;
  onOpen: (card: InsightCard) => void;
}

export function InsightCardsPanel({ isActive, refreshToken, onOpen }: InsightCardsPanelProps) {
  const [cards, setCards] = useState<InsightCard[]>([]);
  const [error, setError] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError('');
    fetchInsightCards()
      .then((items) => {
        if (!cancelled) setCards(items);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '加载洞察卡片失败。');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  const remove = async (card: InsightCard) => {
    setDeletingId(card.id);
    setError('');
    try {
      await deleteInsightCard(card.id);
      setCards((current) => current.filter((item) => item.id !== card.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '删除洞察卡片失败。');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="panel insight-cards" aria-labelledby="insight-cards-title">
      <h2 id="insight-cards-title" className="panel-title">
        已保存洞察
      </h2>
      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title="洞察卡片不可用"
          subtitle={error}
        />
      )}
      {cards.length === 0 && !error ? (
        <p className="muted">还没有保存洞察卡片。</p>
      ) : (
        <ul className="insight-card-list">
          {cards.map((card) => (
            <li key={card.id} className="insight-card-item">
              <strong>{card.question}</strong>
              <p>{card.answer_summary}</p>
              <div className="insight-card-actions">
                <Button
                  kind="ghost"
                  size="sm"
                  renderIcon={Renew}
                  disabled={isActive}
                  onClick={() => onOpen(card)}
                >
                  重新鉴权打开
                </Button>
                <Button
                  kind="ghost"
                  size="sm"
                  hasIconOnly
                  className="insight-card-delete"
                  renderIcon={TrashCan}
                  iconDescription={`删除洞察：${card.question}`}
                  disabled={deletingId === card.id}
                  onClick={() => void remove(card)}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="muted insight-card-note">打开时会按当前账号权限和当前语义版本重新执行。</p>
    </section>
  );
}
