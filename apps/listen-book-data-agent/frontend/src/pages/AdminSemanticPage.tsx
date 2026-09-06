import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Button,
  InlineNotification,
  Modal,
  SkeletonText,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
} from '@carbon/react';
import { Add, ArrowLeft, TrashCan } from '@carbon/icons-react';
import type {
  SemanticColumn,
  SemanticMetric,
  SemanticMetricUpsert,
  SemanticOverview,
  SemanticRelationship,
  SemanticRelationshipUpsert,
  SemanticTable,
} from '../lib/semantic';
import {
  createSemanticMetric,
  createSemanticRelationship,
  deleteSemanticMetric,
  deleteSemanticRelationship,
  fetchSemanticOverview,
  listSemanticColumns,
  listSemanticMetrics,
  listSemanticRelationships,
  listSemanticTables,
  testDatasource,
  updateSemanticColumn,
  updateSemanticMetric,
  updateSemanticRelationship,
  updateSemanticTable,
} from '../lib/semantic';
import {
  ColumnEditModal,
  MetricFormModal,
  RelationshipFormModal,
  TableEditModal,
} from '../components/SemanticEditModals';
import { RebuildRecallPanel } from '../components/RebuildRecallPanel';
import { SemanticReleasePanel } from '../components/SemanticReleasePanel';

interface Feedback {
  kind: 'success' | 'error';
  text: string;
}

const joinCsv = (values: string[]) => values.join('，');

/** 语义层管理（管理员）：数据源、表/字段说明、指标口径。 */
export function AdminSemanticPage() {
  const [overview, setOverview] = useState<SemanticOverview | null>(null);
  const [tables, setTables] = useState<SemanticTable[] | null>(null);
  const [metrics, setMetrics] = useState<SemanticMetric[] | null>(null);
  const [relationships, setRelationships] = useState<SemanticRelationship[] | null>(null);
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);
  const [columns, setColumns] = useState<SemanticColumn[] | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [editingTable, setEditingTable] = useState<SemanticTable | null>(null);
  const [editingColumn, setEditingColumn] = useState<SemanticColumn | null>(null);
  const [metricFormOpen, setMetricFormOpen] = useState(false);
  const [editingMetric, setEditingMetric] = useState<SemanticMetric | undefined>(undefined);
  const [deletingMetric, setDeletingMetric] = useState<SemanticMetric | null>(null);
  const [relationshipFormOpen, setRelationshipFormOpen] = useState(false);
  const [editingRelationship, setEditingRelationship] = useState<SemanticRelationship | undefined>(
    undefined,
  );
  const [deletingRelationship, setDeletingRelationship] = useState<SemanticRelationship | null>(
    null,
  );
  const [formError, setFormError] = useState<string | null>(null);

  const fail = useCallback((err: unknown, fallback: string) => {
    setFeedback({ kind: 'error', text: err instanceof Error ? err.message : fallback });
  }, []);

  const reloadTables = useCallback(async () => {
    try {
      setTables(await listSemanticTables());
    } catch (err) {
      fail(err, '加载数据表失败');
    }
  }, [fail]);

  const reloadMetrics = useCallback(async () => {
    try {
      setMetrics(await listSemanticMetrics());
    } catch (err) {
      fail(err, '加载指标失败');
    }
  }, [fail]);

  const reloadRelationships = useCallback(async () => {
    try {
      setRelationships(await listSemanticRelationships());
    } catch (err) {
      fail(err, '加载关联关系失败');
    }
  }, [fail]);

  const reloadColumns = useCallback(
    async (tableId: string) => {
      setColumns(null);
      try {
        setColumns(await listSemanticColumns(tableId));
      } catch (err) {
        fail(err, '加载字段失败');
      }
    },
    [fail],
  );

  useEffect(() => {
    (async () => {
      try {
        setOverview(await fetchSemanticOverview());
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : '加载总览失败');
      }
      await Promise.all([reloadTables(), reloadMetrics(), reloadRelationships()]);
    })();
  }, [reloadTables, reloadMetrics, reloadRelationships]);

  useEffect(() => {
    if (selectedTableId) void reloadColumns(selectedTableId);
  }, [selectedTableId, reloadColumns]);

  const runDatasourceTest = async (target: 'meta' | 'warehouse', label: string) => {
    setBusy(true);
    setFeedback(null);
    try {
      const result = await testDatasource(target);
      setFeedback(
        result.ok
          ? { kind: 'success', text: `${label}连接正常，延迟 ${result.latency_ms} ms。` }
          : { kind: 'error', text: `${label}连接失败：${result.error ?? '未知错误'}` },
      );
    } catch (err) {
      fail(err, '数据源测试失败');
    } finally {
      setBusy(false);
    }
  };

  const submitTable = async (body: Parameters<typeof updateSemanticTable>[1]) => {
    if (!editingTable) return;
    setBusy(true);
    setFormError(null);
    try {
      await updateSemanticTable(editingTable.id, body);
      setEditingTable(null);
      setFeedback({ kind: 'success', text: '表说明已保存。' });
      await reloadTables();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const submitColumn = async (body: Parameters<typeof updateSemanticColumn>[1]) => {
    if (!editingColumn) return;
    setBusy(true);
    setFormError(null);
    try {
      await updateSemanticColumn(editingColumn.id, body);
      const tableId = editingColumn.table_id;
      setEditingColumn(null);
      setFeedback({ kind: 'success', text: '字段说明已保存。' });
      await reloadColumns(tableId);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const submitMetric = async (id: string | null, body: SemanticMetricUpsert) => {
    setBusy(true);
    setFormError(null);
    try {
      if (id === null && editingMetric) {
        await updateSemanticMetric(editingMetric.id, body);
      } else if (id !== null) {
        await createSemanticMetric({ id, ...body });
      }
      setMetricFormOpen(false);
      setEditingMetric(undefined);
      setFeedback({ kind: 'success', text: '指标口径已保存。' });
      await reloadMetrics();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const confirmDeleteMetric = async () => {
    if (!deletingMetric) return;
    setBusy(true);
    try {
      await deleteSemanticMetric(deletingMetric.id);
      setFeedback({ kind: 'success', text: `指标「${deletingMetric.name}」已删除。` });
      setDeletingMetric(null);
      await reloadMetrics();
    } catch (err) {
      fail(err, '删除失败');
      setDeletingMetric(null);
    } finally {
      setBusy(false);
    }
  };

  const submitRelationship = async (id: string | null, body: SemanticRelationshipUpsert) => {
    setBusy(true);
    setFormError(null);
    try {
      if (editingRelationship) {
        await updateSemanticRelationship(editingRelationship.id, body);
      } else {
        await createSemanticRelationship({ id: id ?? '', ...body });
      }
      setRelationshipFormOpen(false);
      setEditingRelationship(undefined);
      setFeedback({ kind: 'success', text: '关联关系已保存。' });
      await reloadRelationships();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const confirmDeleteRelationship = async () => {
    if (!deletingRelationship) return;
    setBusy(true);
    try {
      await deleteSemanticRelationship(deletingRelationship.id);
      setFeedback({ kind: 'success', text: `关联「${deletingRelationship.id}」已删除。` });
      setDeletingRelationship(null);
      await reloadRelationships();
    } catch (err) {
      fail(err, '删除失败');
      setDeletingRelationship(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-page">
      <header className="admin-header">
        <Link to="/" className="admin-back">
          <ArrowLeft size={16} aria-hidden />
          返回工作台
        </Link>
        <h1 className="admin-title">语义层管理</h1>
        <p className="admin-subtitle">
          维护数据表、字段说明与指标口径。修改立即对 SQL
          校验与生成生效；召回索引的更新将在「重建知识库」功能（下一迭代）中提供。
        </p>
      </header>

      {loadError && (
        <div className="panel admin-panel-wide">
          <InlineNotification kind="error" lowContrast hideCloseButton title={loadError} />
        </div>
      )}

      {feedback && (
        <div className="admin-feedback-wrap">
          <InlineNotification
            kind={feedback.kind}
            lowContrast
            title={feedback.text}
            onCloseButtonClick={() => setFeedback(null)}
          />
        </div>
      )}

      <section className="panel admin-panel-wide" aria-labelledby="datasource-title">
        <h2 className="panel-title" id="datasource-title">
          数据源
        </h2>
        {overview === null ? (
          <SkeletonText paragraph lineCount={2} />
        ) : (
          <>
            <div className="datasource-grid">
              {overview.datasources.map((ds) => (
                <div key={ds.key} className="datasource-card">
                  <div className="datasource-label">{ds.label}</div>
                  <code>
                    {ds.user}@{ds.host}:{ds.port}/{ds.database}
                  </code>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => void runDatasourceTest(ds.key, ds.label)}
                  >
                    测试连接
                  </Button>
                </div>
              ))}
            </div>
            <p className="muted overview-line">
              活跃知识库构建：<code>{overview.active_build_id ?? '无'}</code>
              {' · '}表 {overview.tables} · 字段 {overview.columns} · 指标 {overview.metrics} · 关联{' '}
              {overview.relationships}
            </p>
          </>
        )}
      </section>

      <RebuildRecallPanel />

      <SemanticReleasePanel />

      <section className="panel admin-panel-wide" aria-labelledby="semantic-editor-title">
        <h2 className="panel-title" id="semantic-editor-title">
          元数据与指标
        </h2>
        <Tabs>
          <TabList aria-label="语义层分类">
            <Tab>数据表与字段</Tab>
            <Tab>指标口径{metrics ? `（${metrics.length}）` : ''}</Tab>
            <Tab>关联关系{relationships ? `（${relationships.length}）` : ''}</Tab>
          </TabList>
          <TabPanels>
            <TabPanel>
              <div className="semantic-split">
                <div className="semantic-table-list">
                  {tables === null ? (
                    <SkeletonText paragraph lineCount={4} />
                  ) : (
                    <ul>
                      {tables.map((table) => (
                        <li key={table.id}>
                          <button
                            type="button"
                            className={`semantic-table-item${
                              selectedTableId === table.id ? ' semantic-table-item--active' : ''
                            }`}
                            onClick={() => setSelectedTableId(table.id)}
                          >
                            <span className="semantic-table-name">{table.id}</span>
                            <span className="semantic-table-desc">{table.description}</span>
                          </button>
                          <Button
                            kind="ghost"
                            size="sm"
                            onClick={() => {
                              setFormError(null);
                              setEditingTable(table);
                            }}
                          >
                            编辑
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="semantic-column-list">
                  {selectedTableId === null ? (
                    <p className="muted">从左侧选择一张表查看字段。</p>
                  ) : columns === null ? (
                    <SkeletonText paragraph lineCount={4} />
                  ) : (
                    <Table size="sm">
                      <TableHead>
                        <TableRow>
                          <TableHeader>字段</TableHeader>
                          <TableHeader>类型</TableHeader>
                          <TableHeader>说明</TableHeader>
                          <TableHeader>别名</TableHeader>
                          <TableHeader>标记</TableHeader>
                          <TableHeader>操作</TableHeader>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {columns.map((column) => (
                          <TableRow key={column.id}>
                            <TableCell>
                              <code>{column.name}</code>
                            </TableCell>
                            <TableCell>{column.type}</TableCell>
                            <TableCell>{column.description}</TableCell>
                            <TableCell>{joinCsv(column.alias)}</TableCell>
                            <TableCell>
                              {column.sensitive && (
                                <Tag type="red" size="sm">
                                  敏感
                                </Tag>
                              )}
                              {column.sync && (
                                <Tag type="teal" size="sm">
                                  枚举同步
                                </Tag>
                              )}
                            </TableCell>
                            <TableCell>
                              <Button
                                kind="ghost"
                                size="sm"
                                onClick={() => {
                                  setFormError(null);
                                  setEditingColumn(column);
                                }}
                              >
                                编辑
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </div>
              </div>
            </TabPanel>
            <TabPanel>
              <div className="panel-title-row">
                <span className="muted">指标口径修改后即时参与 SQL 生成与校验。</span>
                <Button
                  kind="primary"
                  size="sm"
                  renderIcon={Add}
                  onClick={() => {
                    setEditingMetric(undefined);
                    setFormError(null);
                    setMetricFormOpen(true);
                  }}
                >
                  新增指标
                </Button>
              </div>
              {metrics === null ? (
                <SkeletonText paragraph lineCount={4} />
              ) : (
                <Table size="md">
                  <TableHead>
                    <TableRow>
                      <TableHeader>编码</TableHeader>
                      <TableHeader>名称</TableHeader>
                      <TableHeader>口径说明</TableHeader>
                      <TableHeader>SQL 公式</TableHeader>
                      <TableHeader>别名</TableHeader>
                      <TableHeader>操作</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {metrics.map((metric) => (
                      <TableRow key={metric.id}>
                        <TableCell>
                          <code>{metric.id}</code>
                        </TableCell>
                        <TableCell>{metric.name}</TableCell>
                        <TableCell>{metric.description}</TableCell>
                        <TableCell>
                          <code>{metric.formula}</code>
                        </TableCell>
                        <TableCell>{joinCsv(metric.alias)}</TableCell>
                        <TableCell>
                          <div className="provider-actions">
                            <Button
                              kind="ghost"
                              size="sm"
                              onClick={() => {
                                setEditingMetric(metric);
                                setFormError(null);
                                setMetricFormOpen(true);
                              }}
                            >
                              编辑
                            </Button>
                            <Button
                              kind="danger--ghost"
                              size="sm"
                              renderIcon={TrashCan}
                              disabled={busy}
                              onClick={() => setDeletingMetric(metric)}
                            >
                              删除
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </TabPanel>
            <TabPanel>
              <div className="panel-title-row">
                <span className="muted">
                  表关联用于 SQL 生成的 JOIN 推理；多态关联可按条件区分目标表。
                </span>
                <Button
                  kind="primary"
                  size="sm"
                  renderIcon={Add}
                  onClick={() => {
                    setEditingRelationship(undefined);
                    setFormError(null);
                    setRelationshipFormOpen(true);
                  }}
                >
                  新增关联
                </Button>
              </div>
              {relationships === null ? (
                <SkeletonText paragraph lineCount={4} />
              ) : (
                <Table size="md">
                  <TableHead>
                    <TableRow>
                      <TableHeader>源</TableHeader>
                      <TableHeader>目标</TableHeader>
                      <TableHeader>类型</TableHeader>
                      <TableHeader>条件</TableHeader>
                      <TableHeader>属性</TableHeader>
                      <TableHeader>操作</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {relationships.map((relationship) => (
                      <TableRow key={relationship.id}>
                        <TableCell>
                          <code>
                            {relationship.source_table}.{relationship.source_column}
                          </code>
                        </TableCell>
                        <TableCell>
                          <code>
                            {relationship.target_table}.{relationship.target_column}
                          </code>
                        </TableCell>
                        <TableCell>{relationship.relationship_type}</TableCell>
                        <TableCell>
                          {relationship.condition ? (
                            <code>{relationship.condition}</code>
                          ) : (
                            <span className="muted">-</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Tag type={relationship.physical ? 'cool-gray' : 'purple'} size="sm">
                            {relationship.physical ? '物理' : '逻辑'}
                          </Tag>
                        </TableCell>
                        <TableCell>
                          <div className="provider-actions">
                            <Button
                              kind="ghost"
                              size="sm"
                              onClick={() => {
                                setEditingRelationship(relationship);
                                setFormError(null);
                                setRelationshipFormOpen(true);
                              }}
                            >
                              编辑
                            </Button>
                            <Button
                              kind="danger--ghost"
                              size="sm"
                              renderIcon={TrashCan}
                              disabled={busy}
                              onClick={() => setDeletingRelationship(relationship)}
                            >
                              删除
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </TabPanel>
          </TabPanels>
        </Tabs>
      </section>

      {editingTable && (
        <TableEditModal
          open
          table={editingTable}
          submitting={busy}
          error={formError}
          onClose={() => setEditingTable(null)}
          onSubmit={(body) => void submitTable(body)}
        />
      )}
      {editingColumn && (
        <ColumnEditModal
          open
          column={editingColumn}
          submitting={busy}
          error={formError}
          onClose={() => setEditingColumn(null)}
          onSubmit={(body) => void submitColumn(body)}
        />
      )}
      <MetricFormModal
        open={metricFormOpen}
        metric={editingMetric}
        submitting={busy}
        error={formError}
        onClose={() => {
          setMetricFormOpen(false);
          setEditingMetric(undefined);
        }}
        onSubmit={(id, body) => void submitMetric(id, body)}
      />
      <Modal
        open={deletingMetric !== null}
        danger
        modalHeading="删除指标"
        primaryButtonText="删除"
        secondaryButtonText="取消"
        onRequestClose={() => setDeletingMetric(null)}
        onRequestSubmit={() => void confirmDeleteMetric()}
      >
        <p>
          确认删除指标「{deletingMetric?.name}」（{deletingMetric?.id}）？删除后不可恢复。
        </p>
      </Modal>
      <RelationshipFormModal
        open={relationshipFormOpen}
        relationship={editingRelationship}
        submitting={busy}
        error={formError}
        onClose={() => {
          setRelationshipFormOpen(false);
          setEditingRelationship(undefined);
        }}
        onSubmit={(id, body) => void submitRelationship(id, body)}
      />
      <Modal
        open={deletingRelationship !== null}
        danger
        modalHeading="删除关联关系"
        primaryButtonText="删除"
        secondaryButtonText="取消"
        onRequestClose={() => setDeletingRelationship(null)}
        onRequestSubmit={() => void confirmDeleteRelationship()}
      >
        <p>确认删除关联「{deletingRelationship?.id}」？删除后不可恢复。</p>
      </Modal>
    </div>
  );
}
