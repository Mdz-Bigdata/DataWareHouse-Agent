import { useCallback, useEffect, useRef, useState } from 'react';
import { HeaderBar } from './components/HeaderBar';
import { Inspector } from './components/Inspector';
import { QueryEditor } from './components/QueryEditor';
import { ResultSection } from './components/ResultSection';
import { Sidebar } from './components/Sidebar';
import { UserMenu } from './components/UserMenu';
import { useQueryController } from './state/useQueryController';
import type { AuthUser } from './lib/auth';
import type { InsightCard } from './lib/insightCards';

type Theme = 'g10' | 'g100';
type MobileTab = 'examples' | 'results' | 'inspector';

export interface AppAuthProps {
  user: AuthUser;
  onLogout: () => void;
  onChangePassword: (oldPassword: string, newPassword: string) => Promise<void>;
  /** 管理员入口回调（react-router navigate），缺省时菜单不显示管理项 */
  onOpenAdmin?: () => void;
  /** 语义层管理入口回调 */
  onOpenSemantic?: () => void;
  /** 查询分析入口回调 */
  onOpenAnalytics?: () => void;
}

/** Carbon v11 编译 CSS 的主题变量挂在 cds-- 前缀的类上。 */
const CARBON_THEME_CLASS: Record<Theme, string> = {
  g10: 'cds--g10',
  g100: 'cds--g100',
};

const THEME_STORAGE_KEY = 'listenbook-theme';

function loadTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === 'g100' ? 'g100' : 'g10';
  } catch {
    return 'g10';
  }
}

const MOBILE_TABS: Array<{ id: MobileTab; label: string }> = [
  { id: 'examples', label: '示例问题' },
  { id: 'results', label: '查询与结果' },
  { id: 'inspector', label: '执行详情' },
];

export default function App({ auth }: { auth: AppAuthProps }) {
  const controller = useQueryController();
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [question, setQuestion] = useState('');
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [mobileTab, setMobileTab] = useState<MobileTab>('results');
  const [insightRefreshToken, setInsightRefreshToken] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // storage may be unavailable; theme simply will not persist
    }
  }, [theme]);

  // Escape closes the inspector drawer (≤1023px layouts).
  useEffect(() => {
    if (!inspectorOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setInspectorOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [inspectorOpen]);

  useEffect(() => {
    if (controller.state.phase === 'restored' || controller.state.phase === 'needs_input') {
      setQuestion(controller.state.question);
      setMobileTab('results');
    }
  }, [controller.state.phase, controller.state.question]);

  const runQuestion = useCallback(
    (value: string) => {
      setQuestion(value);
      setMobileTab('results');
      void controller.run(value);
    },
    [controller.run], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const newAnalysis = useCallback(async () => {
    await controller.newConversation();
    setQuestion('');
    setMobileTab('results');
    inputRef.current?.focus();
  }, [controller.newConversation]); // eslint-disable-line react-hooks/exhaustive-deps

  const openInsight = useCallback(
    (card: InsightCard) => {
      setQuestion(card.question);
      setMobileTab('results');
      void controller.openInsight(card.id, card.question);
    },
    [controller.openInsight], // eslint-disable-line react-hooks/exhaustive-deps
  );

  return (
    <div
      className={`app ${theme} ${CARBON_THEME_CLASS[theme]} app--tab-${mobileTab}${inspectorOpen ? ' app--inspector-open' : ''}`}
    >
      <HeaderBar
        theme={theme}
        onToggleTheme={() => setTheme((value) => (value === 'g10' ? 'g100' : 'g10'))}
        inspectorOpen={inspectorOpen}
        onToggleInspector={() => setInspectorOpen((value) => !value)}
      />

      <div className="tabbar" role="tablist" aria-label="工作区视图">
        {MOBILE_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={mobileTab === tab.id}
            className={`tab${mobileTab === tab.id ? ' tab--active' : ''}`}
            onClick={() => setMobileTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="layout">
        <aside className="region region--sidebar" aria-label="示例与记录">
          <div className="sidebar-scroll">
            <Sidebar
              isActive={controller.isActive}
              conversations={controller.conversations}
              activeConversationId={controller.activeConversationId}
              history={controller.history}
              onNew={() => void newAnalysis()}
              onRunQuestion={runQuestion}
              onSelectConversation={(conversationId) =>
                void controller.selectConversation(conversationId)
              }
              onRenameConversation={(conversationId, title) =>
                void controller.renameConversation(conversationId, title)
              }
              onArchiveConversation={(conversationId) =>
                void controller.archiveConversation(conversationId)
              }
              onRestoreTurn={(entry) => {
                controller.restoreTurn(entry);
                setQuestion(entry.question);
                setMobileTab('results');
              }}
              onRegenerate={(entry) => {
                setQuestion(entry.question);
                setMobileTab('results');
                void controller.regenerate(entry);
              }}
              insightRefreshToken={insightRefreshToken}
              onOpenInsight={openInsight}
            />
          </div>
          <UserMenu
            user={auth.user}
            onLogout={auth.onLogout}
            onChangePassword={auth.onChangePassword}
            onOpenAdmin={auth.onOpenAdmin}
            onOpenSemantic={auth.onOpenSemantic}
            onOpenAnalytics={auth.onOpenAnalytics}
          />
        </aside>

        <main className="region region--center">
          <div className="stack">
            <QueryEditor
              question={question}
              phase={controller.state.phase}
              isActive={controller.isActive}
              inputRef={inputRef}
              onQuestionChange={setQuestion}
              onRun={() => void controller.run(question)}
              onCancel={controller.cancel}
              onRetry={() => void controller.run(question)}
            />
            <ResultSection
              state={controller.state}
              theme={theme}
              onRetry={() => void controller.run(controller.state.question || question)}
              onRunRecommendation={runQuestion}
              onInsightSaved={() => setInsightRefreshToken((value) => value + 1)}
            />
          </div>
        </main>

        <aside className="region region--inspector" aria-label="执行检查器">
          <Inspector state={controller.state} />
        </aside>
      </div>
    </div>
  );
}
