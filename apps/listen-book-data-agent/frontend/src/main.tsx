import { StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import '@carbon/styles/css/styles.css';
import '@carbon/charts/styles.css';
import './styles.css';
import App from './App';
import { RequireAuth } from './components/RequireAuth';
import { AdminAnalyticsPage } from './pages/AdminAnalyticsPage';
import { AdminLlmPage } from './pages/AdminLlmPage';
import { AdminSemanticPage } from './pages/AdminSemanticPage';
import { LoginPage } from './pages/LoginPage';
import { AuthProvider, useAuth } from './state/auth';

/** 工作台：注入当前用户与菜单回调（路由化后 App 本体保持可独立测试）。 */
function Workbench() {
  const { user, logout, changePassword } = useAuth();
  const navigate = useNavigate();
  if (!user) return null; // RequireAuth 已保证 user 存在
  return (
    <App
      auth={{
        user,
        onLogout: () => void logout(),
        onChangePassword: changePassword,
        onOpenAdmin: user.role === 'admin' ? () => navigate('/admin/llm') : undefined,
        onOpenSemantic: user.role === 'admin' ? () => navigate('/admin/semantic') : undefined,
        onOpenAnalytics: user.role === 'admin' ? () => navigate('/admin/analytics') : undefined,
      }}
    />
  );
}

/**
 * 管理页挂在 App 之外，而主题变量（--ink / --accent 等）定义在 cds--g10 / cds--g100
 * 类下；这里按 App 同一个 localStorage 偏好补挂主题类，管理页才能吃到主题。
 */
function ThemedPage({ children }: { children: ReactNode }) {
  useLocation(); // 路由切换时重读主题偏好
  let dark = false;
  try {
    dark = window.localStorage.getItem('listenbook-theme') === 'g100';
  } catch {
    dark = false;
  }
  return <div className={dark ? 'cds--g100' : 'cds--g10'}>{children}</div>;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Workbench />
              </RequireAuth>
            }
          />
          <Route
            path="/admin/llm"
            element={
              <RequireAuth admin>
                <ThemedPage>
                  <AdminLlmPage />
                </ThemedPage>
              </RequireAuth>
            }
          />
          <Route
            path="/admin/semantic"
            element={
              <RequireAuth admin>
                <ThemedPage>
                  <AdminSemanticPage />
                </ThemedPage>
              </RequireAuth>
            }
          />
          <Route
            path="/admin/analytics"
            element={
              <RequireAuth admin>
                <ThemedPage>
                  <AdminAnalyticsPage />
                </ThemedPage>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
