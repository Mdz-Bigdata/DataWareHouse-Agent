import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  InlineNotification,
  SkeletonText,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
} from '@carbon/react';
import type { SemanticRelease } from '../lib/semantic';
import { listSemanticReleases, rollbackSemanticRelease } from '../lib/semantic';

export function SemanticReleasePanel() {
  const [releases, setReleases] = useState<SemanticRelease[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const reload = useCallback(async () => {
    try {
      setReleases(await listSemanticReleases());
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof Error ? error.message : '加载语义发布记录失败',
      });
      setReleases([]);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const rollback = async (release: SemanticRelease) => {
    setBusyId(release.id);
    setMessage(null);
    try {
      const activated = await rollbackSemanticRelease(release.id);
      setMessage({
        kind: 'success',
        text: `已回滚并创建发布版本 v${activated.version}。`,
      });
      await reload();
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof Error ? error.message : '回滚语义发布失败',
      });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="panel admin-panel-wide" aria-labelledby="semantic-release-title">
      <h2 className="panel-title" id="semantic-release-title">
        语义发布与回滚
      </h2>
      <p className="muted">
        每个版本同时固定知识库构建、Query Set 和业务规则集；回滚会创建新的审计版本。
      </p>
      {message && (
        <InlineNotification
          kind={message.kind}
          lowContrast
          title={message.text}
          onCloseButtonClick={() => setMessage(null)}
        />
      )}
      {releases === null ? (
        <SkeletonText paragraph lineCount={3} />
      ) : releases.length === 0 ? (
        <p className="muted">暂无语义发布记录；下一次通过 Golden Suite 的构建会自动发布。</p>
      ) : (
        <Table size="sm">
          <TableHead>
            <TableRow>
              <TableHeader>版本</TableHeader>
              <TableHeader>知识库构建</TableHeader>
              <TableHeader>Query Set</TableHeader>
              <TableHeader>规则集</TableHeader>
              <TableHeader>类型</TableHeader>
              <TableHeader>操作</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {releases.map((release) => (
              <TableRow key={release.id}>
                <TableCell>
                  <strong>v{release.version}</strong>{' '}
                  {release.active && (
                    <Tag type="green" size="sm">
                      活跃
                    </Tag>
                  )}
                </TableCell>
                <TableCell>
                  <code>{release.knowledge_build_id}</code>
                </TableCell>
                <TableCell>v{release.query_set_version ?? '—'}</TableCell>
                <TableCell>v{release.business_rule_set_version ?? '—'}</TableCell>
                <TableCell>{release.release_kind === 'rollback' ? '回滚' : '发布'}</TableCell>
                <TableCell>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={release.active || busyId !== null}
                    onClick={() => void rollback(release)}
                  >
                    {busyId === release.id ? '回滚中…' : '回滚到此版本'}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}
