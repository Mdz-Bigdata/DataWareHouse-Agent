import { useState, type FormEvent } from 'react';
import { Button, InlineNotification, PasswordInput, TextInput } from '@carbon/react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../state/auth';

/** 登录页：墨蓝底 + 居中卡片，与工作台的咨询风视觉一致。 */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请重试。');
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-brand-mark" aria-hidden />
          <h1 className="login-title">听书问数</h1>
          <p className="login-subtitle">自然语言数据查询工作台</p>
        </div>
        <form onSubmit={submit} className="login-form" aria-label="登录">
          {error && (
            <InlineNotification
              kind="error"
              lowContrast
              hideCloseButton
              title={error}
              className="login-error"
            />
          )}
          <TextInput
            id="login-username"
            labelText="用户名"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
          <PasswordInput
            id="login-password"
            labelText="密码"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
          <Button kind="primary" type="submit" className="login-submit" disabled={submitting}>
            {submitting ? '登录中…' : '登录'}
          </Button>
        </form>
      </div>
      <p className="login-footnote">听书问数 · 数据查询工作台</p>
    </div>
  );
}
