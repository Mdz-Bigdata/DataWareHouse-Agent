import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AdminSemanticPage } from '../pages/AdminSemanticPage';

const OVERVIEW = {
  active_build_id: 'build-1',
  build_created_at: '2026-07-17T10:00:00',
  tables: 1,
  columns: 1,
  metrics: 1,
  relationships: 0,
  datasources: [
    {
      key: 'meta',
      label: '元数据库（语义层）',
      host: 'mysql',
      port: 3306,
      database: 'meta',
      user: 'app',
    },
    {
      key: 'warehouse',
      label: '业务数据仓库',
      host: 'mysql',
      port: 3306,
      database: 'audio',
      user: 'reader',
    },
  ],
};

const TABLES = [
  {
    id: 'audio_album',
    name: 'audio_album',
    role: 'fact',
    description: '专辑表',
    alias: ['专辑'],
    domain: 'audio',
  },
];

const COLUMNS = [
  {
    id: 'audio_album.id',
    table_id: 'audio_album',
    name: 'id',
    type: 'bigint',
    role: 'primary_key',
    description: '专辑主键',
    alias: [],
    examples: [],
    nullable: false,
    sensitive: false,
    sync: false,
    enum_values: [],
  },
];

const METRICS = [
  {
    id: 'album_count',
    name: 'album_count',
    description: '专辑总数',
    alias: ['专辑数'],
    formula: 'COUNT(DISTINCT audio_album.id)',
    relevant_columns: ['audio_album.id'],
    filters: [],
    time_column: null,
    unit: 'count',
    dimensions: [],
    snapshot: true,
  },
];

const RELATIONSHIPS = [
  {
    id: 'play_session.album_id->audio_album.id',
    source_table: 'play_session',
    source_column: 'album_id',
    target_table: 'audio_album',
    target_column: 'id',
    relationship_type: 'many_to_one',
    condition: null,
    physical: true,
  },
];

const RELEASES = [
  {
    id: 'release-2',
    version: 2,
    version_label: 'audio-semantic-release-v2',
    domain: 'audio',
    datasource: 'audio',
    release_kind: 'activation',
    knowledge_build_id: 'build-2',
    query_set_id: 'query-set-2',
    query_set_version: 2,
    business_rule_set_id: 'rule-set-2',
    business_rule_set_version: 2,
    source_release_id: null,
    created_by: 'admin',
    created_at: '2026-07-19T20:00:00',
    active: true,
  },
  {
    id: 'release-1',
    version: 1,
    version_label: 'audio-semantic-release-v1',
    domain: 'audio',
    datasource: 'audio',
    release_kind: 'activation',
    knowledge_build_id: 'build-1',
    query_set_id: 'query-set-1',
    query_set_version: 1,
    business_rule_set_id: 'rule-set-1',
    business_rule_set_version: 1,
    source_release_id: null,
    created_by: 'admin',
    created_at: '2026-07-18T20:00:00',
    active: false,
  },
];

function stubFetch() {
  const mock = vi.fn().mockImplementation((url: string) => {
    const path = String(url);
    const json = (body: unknown, status = 200) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    if (path.includes('/overview')) return json(OVERVIEW);
    if (path.includes('/releases/release-1/rollback')) {
      return json({ ...RELEASES[1], id: 'release-3', version: 3, active: true });
    }
    if (path.endsWith('/releases')) return json(RELEASES);
    if (path.includes('/columns')) return json(COLUMNS);
    if (path.includes('/metrics')) return json(METRICS);
    if (path.includes('/relationships')) return json(RELATIONSHIPS);
    if (path.includes('/tables')) return json(TABLES);
    if (path.includes('/datasources/test')) return json({ ok: true, latency_ms: 5, error: null });
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
      <AdminSemanticPage />
    </MemoryRouter>,
  );
}

describe('语义层管理页', () => {
  it('渲染数据源与活跃构建信息', async () => {
    stubFetch();
    renderPage();
    expect(await screen.findByText('元数据库（语义层）')).toBeInTheDocument();
    expect(screen.getByText('业务数据仓库')).toBeInTheDocument();
    expect(screen.getAllByText('build-1').length).toBeGreaterThan(0);
  });

  it('选择数据表后展示字段列表', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('元数据库（语义层）');
    await user.click(screen.getByText('audio_album'));
    expect(await screen.findByText('专辑主键')).toBeInTheDocument();
  });

  it('指标 tab 展示口径列表', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('元数据库（语义层）');
    await user.click(screen.getByRole('tab', { name: /指标口径/ }));
    expect(await screen.findByText('专辑总数')).toBeInTheDocument();
    expect(screen.getByText('COUNT(DISTINCT audio_album.id)')).toBeInTheDocument();
  });

  it('关联关系 tab 展示源与目标', async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('元数据库（语义层）');
    await user.click(screen.getByRole('tab', { name: /关联关系/ }));
    expect(await screen.findByText('play_session.album_id')).toBeInTheDocument();
    expect(screen.getByText('audio_album.id')).toBeInTheDocument();
    expect(screen.getByText('物理')).toBeInTheDocument();
  });

  it('可一键回滚到历史语义发布版本', async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText('语义发布与回滚')).toBeInTheDocument();
    const rollbackButtons = await screen.findAllByRole('button', { name: '回滚到此版本' });
    const enabledButton = rollbackButtons.find((button) => !button.hasAttribute('disabled'));
    expect(enabledButton).toBeDefined();
    await user.click(enabledButton!);
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/releases/release-1/rollback')),
    ).toBe(true);
    expect(await screen.findByText('已回滚并创建发布版本 v3。')).toBeInTheDocument();
  });
});
