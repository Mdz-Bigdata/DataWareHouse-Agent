import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { ChangePasswordForm } from '../components/ChangePasswordForm';
import { useAuth } from '../state/auth';

interface RequireAuthProps {
  children: ReactNode;
  /** 仅管理员可访问 */
  admin?: boolean;
}

/** 路由守卫：未登录跳 /login；非 admin 访问管理页跳回工作台；首登强制改密。 */
export function RequireAuth({ children, admin = false }: RequireAuthProps) {
  const { user, changePassword } = useAuth();
  const location = useLocation();

  if (user === undefined) {
    return (
      <div className="auth-loading" role="status" aria-label="正在恢复会话">
        正在加载…
      </div>
    );
  }
  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (admin && user.role !== 'admin') {
    return <Navigate to="/" replace />;
  }
  if (user.must_change_password) {
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-brand">
            <span className="login-brand-mark" aria-hidden />
            <h1 className="login-title">修改初始密码</h1>
          </div>
          <ChangePasswordForm forced onSubmitPassword={changePassword} onDone={() => undefined} />
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
