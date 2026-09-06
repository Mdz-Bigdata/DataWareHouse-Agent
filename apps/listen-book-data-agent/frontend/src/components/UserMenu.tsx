import { useEffect, useRef, useState } from 'react';
import { Modal } from '@carbon/react';
import { ChevronUp, DataBase, Logout, Password, Settings, UserAvatar } from '@carbon/icons-react';
import { ChartLine } from '@carbon/icons-react';
import type { AuthUser } from '../lib/auth';
import { ChangePasswordForm } from './ChangePasswordForm';

export interface UserMenuProps {
  user: AuthUser;
  onLogout: () => void;
  onChangePassword: (oldPassword: string, newPassword: string) => Promise<void>;
  /** 管理员入口（LLM 供应商配置），未提供时不显示 */
  onOpenAdmin?: () => void;
  /** 管理员入口（语义层管理），未提供时不显示 */
  onOpenSemantic?: () => void;
  /** 管理员入口（查询分析），未提供时不显示 */
  onOpenAnalytics?: () => void;
}

/** 左下角用户菜单：账号信息、修改密码、管理员入口、退出登录。 */
export function UserMenu({
  user,
  onLogout,
  onChangePassword,
  onOpenAdmin,
  onOpenSemantic,
  onOpenAnalytics,
}: UserMenuProps) {
  const [open, setOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={rootRef}>
      {open && (
        <div className="user-menu-popover" role="menu" aria-label="用户菜单">
          <div className="user-menu-identity">
            <span className="user-menu-name">{user.username}</span>
            <span className="user-menu-role">{user.role === 'admin' ? '管理员' : '普通用户'}</span>
          </div>
          <button
            type="button"
            role="menuitem"
            className="user-menu-item"
            onClick={() => {
              setOpen(false);
              setPasswordOpen(true);
            }}
          >
            <Password size={16} aria-hidden />
            修改密码
          </button>
          {user.role === 'admin' && onOpenAdmin && (
            <button
              type="button"
              role="menuitem"
              className="user-menu-item"
              onClick={() => {
                setOpen(false);
                onOpenAdmin();
              }}
            >
              <Settings size={16} aria-hidden />
              LLM 供应商配置
            </button>
          )}
          {user.role === 'admin' && onOpenSemantic && (
            <button
              type="button"
              role="menuitem"
              className="user-menu-item"
              onClick={() => {
                setOpen(false);
                onOpenSemantic();
              }}
            >
              <DataBase size={16} aria-hidden />
              语义层管理
            </button>
          )}
          {user.role === 'admin' && onOpenAnalytics && (
            <button
              type="button"
              role="menuitem"
              className="user-menu-item"
              onClick={() => {
                setOpen(false);
                onOpenAnalytics();
              }}
            >
              <ChartLine size={16} aria-hidden />
              查询分析
            </button>
          )}
          <button type="button" role="menuitem" className="user-menu-item" onClick={onLogout}>
            <Logout size={16} aria-hidden />
            退出登录
          </button>
        </div>
      )}
      <button
        type="button"
        className="user-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <UserAvatar size={20} aria-hidden />
        <span className="user-menu-username">{user.username}</span>
        <ChevronUp
          size={14}
          aria-hidden
          className={`user-menu-caret${open ? ' user-menu-caret--open' : ''}`}
        />
      </button>

      <Modal
        open={passwordOpen}
        modalHeading="修改密码"
        passiveModal
        onRequestClose={() => setPasswordOpen(false)}
      >
        <ChangePasswordForm
          onSubmitPassword={onChangePassword}
          onDone={() => setPasswordOpen(false)}
          onCancel={() => setPasswordOpen(false)}
        />
      </Modal>
    </div>
  );
}
