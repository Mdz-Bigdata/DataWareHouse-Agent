import { useState, type FormEvent } from 'react';
import { Button, InlineNotification, PasswordInput } from '@carbon/react';

interface ChangePasswordFormProps {
  /** 提交修改，抛错时把 message 展示在表单上方 */
  onSubmitPassword: (oldPassword: string, newPassword: string) => Promise<void>;
  /** 强制改密（首次登录）时隐藏取消按钮并展示提示 */
  forced?: boolean;
  onDone: () => void;
  onCancel?: () => void;
}

/** 修改密码表单：用户菜单弹层与首次登录强制改密共用。 */
export function ChangePasswordForm({
  onSubmitPassword,
  forced = false,
  onDone,
  onCancel,
}: ChangePasswordFormProps) {
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (newPassword.length < 6) {
      setError('新密码至少 6 位。');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致。');
      return;
    }
    setSubmitting(true);
    try {
      await onSubmitPassword(oldPassword, newPassword);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : '修改密码失败，请重试。');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="change-password-form" onSubmit={submit} aria-label="修改密码">
      {forced && (
        <InlineNotification
          kind="info"
          lowContrast
          hideCloseButton
          title="首次登录需要修改密码"
          subtitle="修改完成后即可进入工作台。"
          className="change-password-notice"
        />
      )}
      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title={error}
          className="change-password-notice"
        />
      )}
      <PasswordInput
        id="old-password"
        labelText="原密码"
        value={oldPassword}
        onChange={(event) => setOldPassword(event.target.value)}
        required
        autoComplete="current-password"
      />
      <PasswordInput
        id="new-password"
        labelText="新密码（至少 6 位）"
        value={newPassword}
        onChange={(event) => setNewPassword(event.target.value)}
        required
        autoComplete="new-password"
      />
      <PasswordInput
        id="confirm-password"
        labelText="确认新密码"
        value={confirmPassword}
        onChange={(event) => setConfirmPassword(event.target.value)}
        required
        autoComplete="new-password"
      />
      <div className="change-password-actions">
        {!forced && onCancel && (
          <Button kind="ghost" type="button" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
        )}
        <Button kind="primary" type="submit" disabled={submitting}>
          {submitting ? '提交中…' : '确认修改'}
        </Button>
      </div>
    </form>
  );
}
