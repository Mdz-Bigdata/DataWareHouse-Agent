import { apiFetch } from './auth';

export type ProviderType = 'deepseek' | 'openai' | 'openai_compatible';

export interface LlmProvider {
  id: string;
  name: string;
  provider_type: ProviderType;
  base_url: string;
  model_name: string;
  api_key_masked: string;
  temperature: number;
  timeout_seconds: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LlmProviderUpsert {
  name: string;
  provider_type: ProviderType;
  base_url: string;
  model_name: string;
  /** 编辑时留空表示保持原密钥 */
  api_key: string;
  temperature: number;
  timeout_seconds: number;
}

export interface LlmProviderTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

const BASE = '/api/admin/llm-providers';

export const PROVIDER_TYPE_LABELS: Record<ProviderType, string> = {
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
  openai_compatible: 'OpenAI 兼容',
};

async function parseError(response: Response, fallback: string): Promise<Error> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === 'string' && data.detail) return new Error(data.detail);
  } catch {
    // 非 JSON 响应
  }
  return new Error(`${fallback}（HTTP ${response.status}）。`);
}

export async function listProviders(): Promise<LlmProvider[]> {
  const response = await apiFetch(BASE);
  if (!response.ok) throw await parseError(response, '加载供应商列表失败');
  return (await response.json()) as LlmProvider[];
}

export async function createProvider(payload: LlmProviderUpsert): Promise<LlmProvider> {
  const response = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await parseError(response, '新增供应商失败');
  return (await response.json()) as LlmProvider;
}

export async function updateProvider(id: string, payload: LlmProviderUpsert): Promise<LlmProvider> {
  const response = await apiFetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await parseError(response, '保存供应商失败');
  return (await response.json()) as LlmProvider;
}

export async function deleteProvider(id: string): Promise<void> {
  const response = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' });
  if (!response.ok) throw await parseError(response, '删除供应商失败');
}

export async function activateProvider(id: string): Promise<LlmProvider> {
  const response = await apiFetch(`${BASE}/${id}/activate`, { method: 'POST' });
  if (!response.ok) throw await parseError(response, '启用供应商失败');
  return (await response.json()) as LlmProvider;
}

export async function testProvider(
  id: string,
  draft?: LlmProviderUpsert,
): Promise<LlmProviderTestResult> {
  const response = await apiFetch(`${BASE}/${id}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: draft ? JSON.stringify(draft) : undefined,
  });
  if (!response.ok) throw await parseError(response, '连接测试请求失败');
  return (await response.json()) as LlmProviderTestResult;
}
