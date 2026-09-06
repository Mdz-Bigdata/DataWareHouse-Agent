import { Button } from '@carbon/react';
import { Moon, OpenPanelRight, Sun } from '@carbon/icons-react';
import { HealthIndicator } from './HealthIndicator';

interface HeaderBarProps {
  theme: 'g10' | 'g100';
  onToggleTheme: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}

export function HeaderBar({
  theme,
  onToggleTheme,
  inspectorOpen,
  onToggleInspector,
}: HeaderBarProps) {
  return (
    <header className="header">
      <div className="brand">
        <h1 className="brand-title">
          听书问数 <span>工作台</span>
        </h1>
      </div>
      <div className="header-actions">
        <HealthIndicator />
        <Button
          kind="ghost"
          size="md"
          hasIconOnly
          renderIcon={theme === 'g10' ? Moon : Sun}
          iconDescription={theme === 'g10' ? '切换到深色主题' : '切换到浅色主题'}
          onClick={onToggleTheme}
        />
        <Button
          kind="ghost"
          size="md"
          hasIconOnly
          renderIcon={OpenPanelRight}
          iconDescription={inspectorOpen ? '关闭检查器' : '打开检查器'}
          onClick={onToggleInspector}
          className="inspector-toggle"
          aria-expanded={inspectorOpen}
        />
        <a className="debug-link" href="/debug" target="_blank" rel="noreferrer">
          调试页
        </a>
      </div>
    </header>
  );
}
