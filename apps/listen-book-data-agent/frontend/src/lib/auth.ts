/** 认证令牌存取与带鉴权的 fetch 封装。 */

const TOKEN_KEY = 'listenbook-auth-token';

export interface AuthUser {
  id: string;
  username: string;
  role: 'admin' | 'user';
  must_change_password: boolean;
}

export interface LoginResult {
  token: string;
  user: AuthUser;
}

export class AuthError extends Error {}

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // 存储不可用时仅保持内存会话，刷新后需重新登录
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 同上
  }
}

async function parseError(response: Response, fallback: string): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === 'string' && data.detail) return data.detail;
  } catch {
    // 非 JSON 响应
  }
  return `${fallback}（HTTP ${response.status}）。`;
}

/**
 * 统一带 Bearer 头的 fetch。401 时清除令牌并跳转登录页；
 * 登录接口本身传 skipAuthRedirect 以便把 401 作为“密码错误”展示。
 */
export async function apiFetch(
  input: string,
  init: RequestInit = {},
  options: { skipAuthRedirect?: boolean } = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401 && !options.skipAuthRedirect) {
    clearToken();
    if (!window.location.pathname.startsWith('/login')) {
      window.location.assign('/login');
    }
  }
  return response;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  let response: Response;
  try {
    response = await apiFetch(
      '/api/auth/login',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      },
      { skipAuthRedirect: true },
    );
  } catch {
    throw new AuthError('无法连接到认证服务，请检查网络后重试。');
  }
  if (response.status === 401) {
    throw new AuthError('用户名或密码错误。');
  }
  if (!response.ok) {
    throw new AuthError(await parseError(response, '登录失败'));
  }
  const data = (await response.json()) as LoginResult;
  saveToken(data.token);
  return data;
}

export async function fetchMe(): Promise<AuthUser | null> {
  if (!getToken()) return null;
  let response: Response;
  try {
    response = await apiFetch('/api/auth/me', {}, { skipAuthRedirect: true });
  } catch {
    return null;
  }
  if (!response.ok) {
    if (response.status === 401) clearToken();
    return null;
  }
  return (await response.json()) as AuthUser;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' }, { skipAuthRedirect: true });
  } catch {
    // 网络失败也继续本地登出
  }
  clearToken();
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const response = await apiFetch('/api/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  if (!response.ok) {
    throw new AuthError(await parseError(response, '修改密码失败'));
  }
}
