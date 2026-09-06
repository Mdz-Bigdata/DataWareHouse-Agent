import { useEffect, useState } from 'react';
import { Checkbox, InlineNotification, Modal, TextArea, TextInput } from '@carbon/react';
import type {
  SemanticColumn,
  SemanticColumnUpdate,
  SemanticMetric,
  SemanticMetricUpsert,
  SemanticRelationship,
  SemanticRelationshipUpsert,
  SemanticTable,
  SemanticTableUpdate,
} from '../lib/semantic';

/** 列表型字段（别名、关联字段等）统一用逗号分隔输入。 */
const toCsv = (values: string[]) => values.join('，');
const fromCsv = (text: string) =>
  text
    .split(/[,，]/)
    .map((value) => value.trim())
    .filter(Boolean);

interface BaseModalProps {
  open: boolean;
  submitting: boolean;
  error: string | null;
  onClose: () => void;
}

// ==================== 表说明编辑 ====================

interface TableEditModalProps extends BaseModalProps {
  table: SemanticTable;
  onSubmit: (body: SemanticTableUpdate) => void;
}

export function TableEditModal({
  open,
  table,
  submitting,
  error,
  onClose,
  onSubmit,
}: TableEditModalProps) {
  const [description, setDescription] = useState(table.description);
  const [alias, setAlias] = useState(toCsv(table.alias));
  const [role, setRole] = useState(table.role);

  useEffect(() => {
    if (open) {
      setDescription(table.description);
      setAlias(toCsv(table.alias));
      setRole(table.role);
    }
  }, [open, table]);

  return (
    <Modal
      open={open}
      modalHeading={`编辑表说明：${table.id}`}
      primaryButtonText={submitting ? '保存中…' : '保存'}
      secondaryButtonText="取消"
      onRequestClose={onClose}
      onRequestSubmit={() => onSubmit({ description, alias: fromCsv(alias), role })}
    >
      <div className="semantic-form">
        {error && <InlineNotification kind="error" lowContrast hideCloseButton title={error} />}
        <TextInput
          id="table-role"
          labelText="表类型（fact / dimension）"
          value={role}
          onChange={(event) => setRole(event.target.value)}
        />
        <TextArea
          id="table-description"
          labelText="表说明"
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <TextInput
          id="table-alias"
          labelText="业务别名（逗号分隔）"
          value={alias}
          onChange={(event) => setAlias(event.target.value)}
        />
      </div>
    </Modal>
  );
}

// ==================== 字段说明编辑 ====================

interface ColumnEditModalProps extends BaseModalProps {
  column: SemanticColumn;
  onSubmit: (body: SemanticColumnUpdate) => void;
}

export function ColumnEditModal({
  open,
  column,
  submitting,
  error,
  onClose,
  onSubmit,
}: ColumnEditModalProps) {
  const [description, setDescription] = useState(column.description);
  const [alias, setAlias] = useState(toCsv(column.alias));
  const [sensitive, setSensitive] = useState(column.sensitive);
  const [sync, setSync] = useState(column.sync);

  useEffect(() => {
    if (open) {
      setDescription(column.description);
      setAlias(toCsv(column.alias));
      setSensitive(column.sensitive);
      setSync(column.sync);
    }
  }, [open, column]);

  return (
    <Modal
      open={open}
      modalHeading={`编辑字段：${column.id}`}
      primaryButtonText={submitting ? '保存中…' : '保存'}
      secondaryButtonText="取消"
      onRequestClose={onClose}
      onRequestSubmit={() => onSubmit({ description, alias: fromCsv(alias), sensitive, sync })}
    >
      <div className="semantic-form">
        {error && <InlineNotification kind="error" lowContrast hideCloseButton title={error} />}
        <TextArea
          id="column-description"
          labelText="字段说明"
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <TextInput
          id="column-alias"
          labelText="业务别名（逗号分隔）"
          value={alias}
          onChange={(event) => setAlias(event.target.value)}
        />
        <Checkbox
          id="column-sensitive"
          labelText="敏感字段（禁止参与问数）"
          checked={sensitive}
          onChange={(_event, { checked }) => setSensitive(checked)}
        />
        <Checkbox
          id="column-sync"
          labelText="同步枚举值到全文索引"
          checked={sync}
          onChange={(_event, { checked }) => setSync(checked)}
        />
      </div>
    </Modal>
  );
}

// ==================== 指标口径表单 ====================

interface MetricFormModalProps extends BaseModalProps {
  /** 传入表示编辑，缺省表示新增 */
  metric?: SemanticMetric;
  onSubmit: (id: string | null, body: SemanticMetricUpsert) => void;
}

export function MetricFormModal({
  open,
  metric,
  submitting,
  error,
  onClose,
  onSubmit,
}: MetricFormModalProps) {
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [alias, setAlias] = useState('');
  const [formula, setFormula] = useState('');
  const [relevantColumns, setRelevantColumns] = useState('');
  const [filters, setFilters] = useState('');
  const [timeColumn, setTimeColumn] = useState('');
  const [unit, setUnit] = useState('count');
  const [dimensions, setDimensions] = useState('');
  const [snapshot, setSnapshot] = useState(false);

  useEffect(() => {
    if (!open) return;
    setId(metric?.id ?? '');
    setName(metric?.name ?? '');
    setDescription(metric?.description ?? '');
    setAlias(toCsv(metric?.alias ?? []));
    setFormula(metric?.formula ?? '');
    setRelevantColumns(toCsv(metric?.relevant_columns ?? []));
    setFilters(toCsv(metric?.filters ?? []));
    setTimeColumn(metric?.time_column ?? '');
    setUnit(metric?.unit ?? 'count');
    setDimensions(toCsv(metric?.dimensions ?? []));
    setSnapshot(metric?.snapshot ?? false);
  }, [open, metric]);

  const valid =
    name.trim() !== '' && formula.trim() !== '' && (metric !== undefined || id.trim() !== '');

  const submit = () => {
    if (!valid || submitting) return;
    onSubmit(metric ? null : id.trim(), {
      name: name.trim(),
      description,
      alias: fromCsv(alias),
      formula: formula.trim(),
      relevant_columns: fromCsv(relevantColumns),
      filters: fromCsv(filters),
      time_column: timeColumn.trim() || null,
      unit: unit.trim() || 'count',
      dimensions: fromCsv(dimensions),
      snapshot,
    });
  };

  return (
    <Modal
      open={open}
      modalHeading={metric ? `编辑指标：${metric.id}` : '新增指标'}
      primaryButtonText={submitting ? '保存中…' : '保存'}
      secondaryButtonText="取消"
      primaryButtonDisabled={!valid || submitting}
      onRequestClose={onClose}
      onRequestSubmit={submit}
    >
      <div className="semantic-form">
        {error && <InlineNotification kind="error" lowContrast hideCloseButton title={error} />}
        {!metric && (
          <TextInput
            id="metric-id"
            labelText="指标编码（小写下划线，如 play_count）"
            value={id}
            onChange={(event) => setId(event.target.value)}
          />
        )}
        <TextInput
          id="metric-name"
          labelText="指标名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <TextArea
          id="metric-description"
          labelText="口径说明"
          rows={2}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <TextArea
          id="metric-formula"
          labelText="SQL 公式（如 COUNT(DISTINCT audio_album.id)）"
          rows={2}
          value={formula}
          onChange={(event) => setFormula(event.target.value)}
        />
        <TextInput
          id="metric-alias"
          labelText="业务别名（逗号分隔）"
          value={alias}
          onChange={(event) => setAlias(event.target.value)}
        />
        <TextInput
          id="metric-relevant-columns"
          labelText="关联字段（逗号分隔，如 audio_album.id）"
          value={relevantColumns}
          onChange={(event) => setRelevantColumns(event.target.value)}
        />
        <TextInput
          id="metric-filters"
          labelText="固定过滤条件（逗号分隔 SQL 片段，可空）"
          value={filters}
          onChange={(event) => setFilters(event.target.value)}
        />
        <div className="provider-form-row">
          <TextInput
            id="metric-time-column"
            labelText="默认时间字段（可空）"
            value={timeColumn}
            onChange={(event) => setTimeColumn(event.target.value)}
          />
          <TextInput
            id="metric-unit"
            labelText="单位"
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
          />
        </div>
        <TextInput
          id="metric-dimensions"
          labelText="推荐分析维度（逗号分隔，可空）"
          value={dimensions}
          onChange={(event) => setDimensions(event.target.value)}
        />
        <Checkbox
          id="metric-snapshot"
          labelText="当前快照指标（只用于存量，不用于趋势）"
          checked={snapshot}
          onChange={(_event, { checked }) => setSnapshot(checked)}
        />
      </div>
    </Modal>
  );
}

// ==================== 关联关系表单 ====================

interface RelationshipFormModalProps extends BaseModalProps {
  /** 传入表示编辑，缺省表示新增 */
  relationship?: SemanticRelationship;
  onSubmit: (id: string | null, body: SemanticRelationshipUpsert) => void;
}

export function RelationshipFormModal({
  open,
  relationship,
  submitting,
  error,
  onClose,
  onSubmit,
}: RelationshipFormModalProps) {
  const [sourceTable, setSourceTable] = useState('');
  const [sourceColumn, setSourceColumn] = useState('');
  const [targetTable, setTargetTable] = useState('');
  const [targetColumn, setTargetColumn] = useState('');
  const [relationshipType, setRelationshipType] = useState('many_to_one');
  const [condition, setCondition] = useState('');
  const [physical, setPhysical] = useState(true);

  useEffect(() => {
    if (!open) return;
    setSourceTable(relationship?.source_table ?? '');
    setSourceColumn(relationship?.source_column ?? '');
    setTargetTable(relationship?.target_table ?? '');
    setTargetColumn(relationship?.target_column ?? '');
    setRelationshipType(relationship?.relationship_type ?? 'many_to_one');
    setCondition(relationship?.condition ?? '');
    setPhysical(relationship?.physical ?? true);
  }, [open, relationship]);

  const valid =
    sourceTable.trim() !== '' &&
    sourceColumn.trim() !== '' &&
    targetTable.trim() !== '' &&
    targetColumn.trim() !== '';

  const submit = () => {
    if (!valid || submitting) return;
    onSubmit(relationship ? null : '', {
      source_table: sourceTable.trim(),
      source_column: sourceColumn.trim(),
      target_table: targetTable.trim(),
      target_column: targetColumn.trim(),
      relationship_type: relationshipType.trim() || 'many_to_one',
      condition: condition.trim() || null,
      physical,
    });
  };

  return (
    <Modal
      open={open}
      modalHeading={relationship ? `编辑关联：${relationship.id}` : '新增关联关系'}
      primaryButtonText={submitting ? '保存中…' : '保存'}
      secondaryButtonText="取消"
      primaryButtonDisabled={!valid || submitting}
      onRequestClose={onClose}
      onRequestSubmit={submit}
    >
      <div className="semantic-form">
        {error && <InlineNotification kind="error" lowContrast hideCloseButton title={error} />}
        <div className="provider-form-row">
          <TextInput
            id="rel-source-table"
            labelText="源表"
            placeholder="play_session"
            value={sourceTable}
            onChange={(event) => setSourceTable(event.target.value)}
          />
          <TextInput
            id="rel-source-column"
            labelText="源字段"
            placeholder="album_id"
            value={sourceColumn}
            onChange={(event) => setSourceColumn(event.target.value)}
          />
        </div>
        <div className="provider-form-row">
          <TextInput
            id="rel-target-table"
            labelText="目标表"
            placeholder="audio_album"
            value={targetTable}
            onChange={(event) => setTargetTable(event.target.value)}
          />
          <TextInput
            id="rel-target-column"
            labelText="目标字段"
            placeholder="id"
            value={targetColumn}
            onChange={(event) => setTargetColumn(event.target.value)}
          />
        </div>
        <TextInput
          id="rel-type"
          labelText="关联类型"
          value={relationshipType}
          onChange={(event) => setRelationshipType(event.target.value)}
        />
        <TextInput
          id="rel-condition"
          labelText="附加条件（SQL 片段，可空）"
          placeholder="play_session.deleted = 0"
          value={condition}
          onChange={(event) => setCondition(event.target.value)}
        />
        <Checkbox
          id="rel-physical"
          labelText="物理外键（数据库真实存在的约束）"
          checked={physical}
          onChange={(_event, { checked }) => setPhysical(checked)}
        />
      </div>
    </Modal>
  );
}
