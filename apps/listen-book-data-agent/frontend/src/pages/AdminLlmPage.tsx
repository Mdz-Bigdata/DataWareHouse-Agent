import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Button,
  InlineNotification,
  Modal,
  SkeletonText,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
} from '@carbon/react';
import { Add, ArrowLeft, Checkmark, Play, TrashCan } from '@carbon/icons-react';
import type { LlmProvider, LlmProviderUpsert } from '../lib/llmProviders';
import {
  activateProvider,
  createProvider,
  deleteProvider,
  listProviders,
  PROVIDER_TYPE_LABELS,
  testProvider,
  updateProvider,
} from '../lib/llmProviders';
import { ProviderFormModal } from '../components/ProviderFormModal';

interface TestFeedback {
  kind: 'success' | 'error';
  text: string;
}

/** LLM 供应商配置（管理员）：列表、新增/编辑、删除、启用、连接测试。 */
export function AdminLlmPage() {
  const [providers, setProviders] = useState<LlmProvider[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<LlmProvider | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<LlmProvider | null>(null);
  const [feedback, setFeedback] = useState<TestFeedback | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setProviders(await listProviders());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '加载失败');
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openCreate = () => {
    setEditing(undefined);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (provider: LlmProvider) => {
    setEditing(provider);
    setFormError(null);
    setFormOpen(true);
  };

  const submitForm = async (payload: LlmProviderUpsert) => {
    setSubmitting(true);
    setFormError(null);
    try {
      if (editing) {
        await updateProvider(editing.id, payload);
      } else {
        await createProvider(payload);
      }
      setFormOpen(false);
      await reload();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusyId(deleting.id);
    try {
      await deleteProvider(deleting.id);
      setDeleting(null);
      await reload();
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : '删除失败',
      });
      setDeleting(null);
    } finally {
      setBusyId(null);
    }
  };

  const runTest = async (provider: LlmProvider) => {
    setBusyId(provider.id);
    setFeedback(null);
    try {
      const result = await testProvider(provider.id);
      setFeedback(
        result.ok
          ? {
              kind: 'success',
              text: `「${provider.name}」连接成功，延迟 ${result.latency_ms} ms。`,
            }
          : {
              kind: 'error',
              text: `「${provider.name}」连接失败：${result.error ?? '未知错误'}`,
            },
      );
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : '连接测试失败',
      });
    } finally {
      setBusyId(null);
    }
  };

  const activate = async (provider: LlmProvider) => {
    setBusyId(provider.id);
    setFeedback(null);
    try {
      await activateProvider(provider.id);
      setFeedback({
        kind: 'success',
        text: `已启用「${provider.name}」，下一次查询开始生效，无需重启。`,
      });
      await reload();
    } catch (err) {
      setFeedback({
        kind: 'error',
        text: err instanceof Error ? err.message : '启用失败',
      });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="admin-page">
      <header className="admin-header">
        <Link to="/" className="admin-back">
          <ArrowLeft size={16} aria-hidden />
          返回工作台
        </Link>
        <h1 className="admin-title">LLM 供应商配置</h1>
        <p className="admin-subtitle">
          新增、编辑、启用供应商；切换后下一次查询即生效。API Key 加密存储，页面只显示脱敏串。
        </p>
      </header>

      <div className="panel admin-panel-wide">
        <div className="panel-title-row">
          <h2 className="panel-title">供应商列表</h2>
          <Button kind="primary" size="sm" renderIcon={Add} onClick={openCreate}>
            新增供应商
          </Button>
        </div>

        {feedback && (
          <InlineNotification
            kind={feedback.kind}
            lowContrast
            hideCloseButton={false}
            title={feedback.text}
            onCloseButtonClick={() => setFeedback(null)}
            className="admin-feedback"
          />
        )}
        {loadError && (
          <InlineNotification kind="error" lowContrast hideCloseButton title={loadError} />
        )}

        {providers === null && !loadError ? (
          <SkeletonText paragraph lineCount={3} />
        ) : (
          <Table size="md" className="provider-table">
            <TableHead>
              <TableRow>
                <TableHeader>名称</TableHeader>
                <TableHeader>类型</TableHeader>
                <TableHeader>模型</TableHeader>
                <TableHeader>Base URL</TableHeader>
                <TableHeader>API Key</TableHeader>
                <TableHeader>状态</TableHeader>
                <TableHeader>操作</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {(providers ?? []).map((provider) => (
                <TableRow key={provider.id}>
                  <TableCell>{provider.name}</TableCell>
                  <TableCell>{PROVIDER_TYPE_LABELS[provider.provider_type]}</TableCell>
                  <TableCell>
                    <code>{provider.model_name}</code>
                  </TableCell>
                  <TableCell>
                    <code className="provider-url">{provider.base_url}</code>
                  </TableCell>
                  <TableCell>
                    <code>{provider.api_key_masked}</code>
                  </TableCell>
                  <TableCell>
                    {provider.is_active ? (
                      <Tag type="green" size="sm" renderIcon={Checkmark}>
                        启用中
                      </Tag>
                    ) : (
                      <Tag type="cool-gray" size="sm">
                        未启用
                      </Tag>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="provider-actions">
                      <Button
                        kind="ghost"
                        size="sm"
                        renderIcon={Play}
                        disabled={busyId !== null}
                        onClick={() => void runTest(provider)}
                      >
                        测试
                      </Button>
                      {!provider.is_active && (
                        <Button
                          kind="ghost"
                          size="sm"
                          disabled={busyId !== null}
                          onClick={() => void activate(provider)}
                        >
                          启用
                        </Button>
                      )}
                      <Button
                        kind="ghost"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => openEdit(provider)}
                      >
                        编辑
                      </Button>
                      <Button
                        kind="danger--ghost"
                        size="sm"
                        renderIcon={TrashCan}
                        disabled={busyId !== null || provider.is_active}
                        onClick={() => setDeleting(provider)}
                      >
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {(providers ?? []).length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="muted">
                    暂无供应商，点击右上角「新增供应商」添加。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </div>

      <ProviderFormModal
        open={formOpen}
        provider={editing}
        submitting={submitting}
        error={formError}
        onClose={() => setFormOpen(false)}
        onSubmit={(payload) => void submitForm(payload)}
      />

      <Modal
        open={deleting !== null}
        danger
        modalHeading="删除供应商"
        primaryButtonText="删除"
        secondaryButtonText="取消"
        onRequestClose={() => setDeleting(null)}
        onRequestSubmit={() => void confirmDelete()}
      >
        <p>确认删除「{deleting?.name}」？删除后不可恢复。</p>
      </Modal>
    </div>
  );
}
