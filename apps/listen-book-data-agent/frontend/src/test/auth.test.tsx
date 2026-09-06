import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { LoginPage } from '../pages/LoginPage';
import { AuthProvider } from '../state/auth';
import { getToken } from '../lib/auth';

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function stubLoginResponse(status: number, body: Record<string, unknown> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('登录页', () => {
  it('渲染用户名、密码与登录按钮', () => {
    stubLoginResponse(401);
    renderLogin();
    expect(screen.getByLabelText('用户名')).toBeInTheDocument();
    expect(screen.getByLabelText('密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
  });

  it('密码错误时展示错误提示且不保存令牌', async () => {
    stubLoginResponse(401);
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText('用户名'), 'admin');
    await user.type(screen.getByLabelText('密码'), 'wrong');
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText('用户名或密码错误。')).toBeInTheDocument();
    expect(getToken()).toBeNull();
  });

  it('登录成功保存令牌', async () => {
    stubLoginResponse(200, {
      token: 'jwt-token-1',
      token_type: 'bearer',
      expires_in_minutes: 720,
      user: { id: 'u1', username: 'admin', role: 'admin', must_change_password: false },
    });
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText('用户名'), 'admin');
    await user.type(screen.getByLabelText('密码'), 'admin123');
    await user.click(screen.getByRole('button', { name: '登录' }));
    await waitFor(() => expect(getToken()).toBe('jwt-token-1'));
  });
});
