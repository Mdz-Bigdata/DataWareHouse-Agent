import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AdminLlmPage } from '../pages/AdminLlmPage';

const PROVIDERS = [
  {
    id: 'p1',
    name: 'DeepSeek 生产',
    provider_type: 'deepseek',
    base_url: 'https://api.deepseek.com',
    model_name: 'deepseek-chat',
    api_key_masked: 'sk-****7890',
    temperature: 0,
    timeout_seconds: 60,
    is_active: true,
    created_at: '2026-07-17T10:00:00',
    updated_at: '2026-07-17T10:00:00',
  },
  {
    id: 'p2',
    name: 'OpenAI 备用',
    provider_type: 'openai',
    base_url: 'https://api.openai.com/v1',
    model_name: 'gpt-4o',
    api_key_masked: 'sk-****abcd',
    temperature: 0,
    timeout_seconds: 60,
    is_active: false,
    created_at: '2026-07-17T10:00:00',
    updated_at: '2026-07-17T10:00:00',
  },
];

function stubFetch() {
  const mock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = String(url);
    const json = (body: unknown, status = 200) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    if (path.endsWith('/api/admin/llm-providers') && (!init || init.method === undefined)) {
      return json(PROVIDERS);
    }
    if (path.includes('/activate')) {
      return json({ ...PROVIDERS[1], is_active: true });
    }
    if (path.includes('/test')) {
      return json({ ok: true, latency_ms: 230, error: null });
    }
    return json({}, 404);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminLlmPage />
    </MemoryRouter>,
  );
}

describe('LLM 供应商配置页', () => {
  it('渲染供应商列表与脱敏 Key', async () => {
    stubFetch();
    renderPage();
    expect(await screen.findByText('DeepSeek 生产')).toBeInTheDocument();
    expect(screen.getByText('OpenAI 备用')).toBeInTheDocument();
    expect(screen.getByText('sk-****7890')).toBeInTheDocument();
    expect(screen.getByText('启用中')).toBeInTheDocument();
  });

  it('点击测试连接展示成功反馈', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('DeepSeek 生产');
    const testButtons = screen.getAllByRole('button', { name: '测试' });
    await user.click(testButtons[0]);
    expect(await screen.findByText(/连接成功，延迟 230 ms/)).toBeInTheDocument();
  });

  it('新增按钮打开表单弹窗，新增时密钥必填', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('DeepSeek 生产');
    await user.click(screen.getByRole('button', { name: '新增供应商' }));
    expect(await screen.findByLabelText('API Key')).toBeInTheDocument();
    const save = screen.getByRole('button', { name: '保存' });
    expect(save).toBeDisabled();
  });

  it('启用中的供应商不允许删除', async () => {
    stubFetch();
    renderPage();
    await screen.findByText('DeepSeek 生产');
    const deleteButtons = screen.getAllByRole('button', { name: '删除' });
    expect(deleteButtons[0]).toBeDisabled(); // p1 启用中
    expect(deleteButtons[1]).not.toBeDisabled(); // p2 未启用
  });
});
