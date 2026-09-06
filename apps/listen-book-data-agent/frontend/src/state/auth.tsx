import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  type AuthUser,
  changePassword as changePasswordApi,
  fetchMe,
  login as loginApi,
  logout as logoutApi,
} from '../lib/auth';

interface AuthContextValue {
  /** undefined = 正在恢复会话；null = 未登录 */
  user: AuthUser | null | undefined;
  login: (username: string, password: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
  changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    void fetchMe().then((me) => {
      if (!cancelled) setUser(me);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await loginApi(username, password);
    setUser(result.user);
    return result.user;
  }, []);

  const logout = useCallback(async () => {
    await logoutApi();
    setUser(null);
  }, []);

  const changePassword = useCallback(async (oldPassword: string, newPassword: string) => {
    await changePasswordApi(oldPassword, newPassword);
    // 改密成功后 must_change_password 复位，同步本地用户态
    const me = await fetchMe();
    setUser(me);
  }, []);

  const value = useMemo(
    () => ({ user, login, logout, changePassword }),
    [user, login, logout, changePassword],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth 必须在 AuthProvider 内使用');
  return context;
}
