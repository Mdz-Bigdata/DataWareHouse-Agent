import { useEffect, useState } from 'react';
import {
  InlineNotification,
  Modal,
  NumberInput,
  PasswordInput,
  Select,
  SelectItem,
  TextInput,
} from '@carbon/react';
import type { LlmProvider, LlmProviderUpsert, ProviderType } from '../lib/llmProviders';
import { PROVIDER_TYPE_LABELS } from '../lib/llmProviders';

interface ProviderFormModalProps {
  open: boolean;
  /** 传入表示编辑，缺省表示新增 */
  provider?: LlmProvider;
  submitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: LlmProviderUpsert) => void;
}

interface FormState {
  name: string;
  provider_type: ProviderType;
  base_url: string;
  model_name: string;
  api_key: string;
  temperature: number;
  timeout_seconds: number;
}

const DEFAULT_BASE_URLS: Record<ProviderType, string> = {
  deepseek: 'https://api.deepseek.com',
  openai: 'https://api.openai.com/v1',
  openai_compatible: '',
};

function toFormState(provider?: LlmProvider): FormState {
  if (!provider) {
    return {
      name: '',
      provider_type: 'deepseek',
      base_url: DEFAULT_BASE_URLS.deepseek,
      model_name: '',
      api_key: '',
      temperature: 0,
      timeout_seconds: 60,
    };
  }
  return {
    name: provider.name,
    provider_type: provider.provider_type,
    base_url: provider.base_url,
    model_name: provider.model_name,
    api_key: '',
    temperature: provider.temperature,
    timeout_seconds: provider.timeout_seconds,
  };
}

export function ProviderFormModal({
  open,
  provider,
  submitting,
  error,
  onClose,
  onSubmit,
}: ProviderFormModalProps) {
  const [form, setForm] = useState<FormState>(() => toFormState(provider));

  useEffect(() => {
    if (open) setForm(toFormState(provider));
  }, [open, provider]);

  const patch = (partial: Partial<FormState>) => setForm((value) => ({ ...value, ...partial }));

  const valid =
    form.name.trim() !== '' &&
    form.base_url.trim() !== '' &&
    form.model_name.trim() !== '' &&
    (provider !== undefined || form.api_key.trim() !== '');

  const submit = () => {
    if (!valid || submitting) return;
    onSubmit({
      name: form.name.trim(),
      provider_type: form.provider_type,
      base_url: form.base_url.trim(),
      model_name: form.model_name.trim(),
      api_key: form.api_key.trim(),
      temperature: form.temperature,
      timeout_seconds: form.timeout_seconds,
    });
  };

  return (
    <Modal
      open={open}
      modalHeading={provider ? `编辑供应商：${provider.name}` : '新增供应商'}
      primaryButtonText={submitting ? '保存中…' : '保存'}
      secondaryButtonText="取消"
      primaryButtonDisabled={!valid || submitting}
      onRequestClose={onClose}
      onRequestSubmit={submit}
    >
      <div className="provider-form">
        {error && <InlineNotification kind="error" lowContrast hideCloseButton title={error} />}
        <TextInput
          id="provider-name"
          labelText="名称"
          placeholder="例如：DeepSeek 生产环境"
          value={form.name}
          onChange={(event) => patch({ name: event.target.value })}
        />
        <Select
          id="provider-type"
          labelText="供应商类型"
          value={form.provider_type}
          onChange={(event) => {
            const providerType = event.target.value as ProviderType;
            patch({
              provider_type: providerType,
              base_url:
                form.base_url === '' || Object.values(DEFAULT_BASE_URLS).includes(form.base_url)
                  ? DEFAULT_BASE_URLS[providerType]
                  : form.base_url,
            });
          }}
        >
          {(Object.keys(PROVIDER_TYPE_LABELS) as ProviderType[]).map((type) => (
            <SelectItem key={type} value={type} text={PROVIDER_TYPE_LABELS[type]} />
          ))}
        </Select>
        <TextInput
          id="provider-base-url"
          labelText="Base URL"
          placeholder="https://api.deepseek.com"
          value={form.base_url}
          onChange={(event) => patch({ base_url: event.target.value })}
        />
        <TextInput
          id="provider-model"
          labelText="模型"
          placeholder="deepseek-chat"
          value={form.model_name}
          onChange={(event) => patch({ model_name: event.target.value })}
        />
        <PasswordInput
          id="provider-api-key"
          labelText={provider ? 'API Key（留空保持不变）' : 'API Key'}
          placeholder={provider ? provider.api_key_masked : 'sk-...'}
          value={form.api_key}
          onChange={(event) => patch({ api_key: event.target.value })}
          autoComplete="new-password"
        />
        <div className="provider-form-row">
          <NumberInput
            id="provider-temperature"
            label="Temperature"
            min={0}
            max={2}
            step={0.1}
            value={form.temperature}
            onChange={(_event, { value }) => patch({ temperature: Number(value) })}
          />
          <NumberInput
            id="provider-timeout"
            label="超时（秒）"
            min={5}
            max={600}
            step={5}
            value={form.timeout_seconds}
            onChange={(_event, { value }) => patch({ timeout_seconds: Number(value) })}
          />
        </div>
      </div>
    </Modal>
  );
}
