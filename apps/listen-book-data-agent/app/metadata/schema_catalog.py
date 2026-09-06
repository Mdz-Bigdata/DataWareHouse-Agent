from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from omegaconf import OmegaConf

from app.conf.meta_config import ColumnConfig, MetaConfig, TableConfig
from app.entities.column_info import ColumnInfo
from app.entities.relationship_info import RelationshipInfo
from app.entities.table_info import TableInfo

CREATE_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?P<name>[a-zA-Z0-9_]+)\s*\("
    r"(?P<body>.*?)\n\)\s*ENGINE\s*=\s*InnoDB.*?"
    r"COMMENT\s*=\s*'(?P<comment>[^']*)'\s*;",
    re.IGNORECASE | re.DOTALL,
)
COLUMN_PATTERN = re.compile(
    r"^(?P<name>[a-z_][a-z0-9_]*)\s+"
    r"(?P<type>[A-Z]+(?:\([^)]*\))?)(?P<rest>.*)$"
)
FOREIGN_KEY_PATTERN = re.compile(
    r"CONSTRAINT\s+(?P<name>[a-zA-Z0-9_]+)\s+FOREIGN\s+KEY\s*"
    r"\(\s*(?P<source_column>[a-zA-Z0-9_]+)\s*\)\s+REFERENCES\s+"
    r"(?P<target_table>[a-zA-Z0-9_]+)\s*"
    r"\(\s*(?P<target_column>[a-zA-Z0-9_]+)\s*\)",
    re.IGNORECASE | re.DOTALL,
)

FIELD_LABELS = {
    "id": "ID",
    "user_id": "用户ID",
    "album_id": "专辑ID",
    "track_id": "章节ID",
    "category_id": "分类ID",
    "tag_id": "标签ID",
    "channel_id": "渠道ID",
    "currency_code": "币种",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "published_at": "发布时间",
    "paid_at": "支付时间",
    "refunded_at": "退款时间",
    "play_start_at": "播放开始时间",
    "play_end_at": "播放结束时间",
    "stat_date": "统计日期",
    "yn": "是否启用",
}
TOKEN_LABELS = {
    "user": "用户",
    "account": "账户",
    "audio": "音频",
    "album": "专辑",
    "track": "章节",
    "content": "内容",
    "order": "订单",
    "payment": "支付",
    "refund": "退款",
    "member": "会员",
    "vip": "VIP",
    "wallet": "钱包",
    "play": "播放",
    "listening": "收听",
    "comment": "评论",
    "rating": "评分",
    "reaction": "互动",
    "report": "举报",
    "search": "搜索",
    "keyword": "关键词",
    "ranking": "榜单",
    "recommend": "推荐",
    "topic": "专题",
    "author": "作者",
    "narrator": "主播",
    "organization": "机构",
    "creator": "创作者",
    "category": "分类",
    "tag": "标签",
    "channel": "渠道",
    "language": "语言",
    "currency": "币种",
    "status": "状态",
    "type": "类型",
    "name": "名称",
    "title": "标题",
    "count": "数量",
    "amount": "金额",
    "duration": "时长",
    "seconds": "秒数",
    "score": "分数",
    "flag": "标记",
    "time": "时间",
    "date": "日期",
    "at": "时间",
    "no": "编号",
    "code": "编码",
    "id": "ID",
}


@dataclass
class PhysicalColumn:
    name: str
    data_type: str
    nullable: bool
    primary_key: bool
    comment: str = ""
    foreign_key: bool = False
    enum_values: list[str] = field(default_factory=list)


@dataclass
class PhysicalRelationship:
    name: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str


@dataclass
class PhysicalTable:
    name: str
    comment: str
    columns: dict[str, PhysicalColumn]


@dataclass
class PhysicalCatalog:
    tables: dict[str, PhysicalTable]
    relationships: list[PhysicalRelationship]


@dataclass
class DomainMetadata:
    tables: list[TableInfo]
    columns: list[ColumnInfo]
    relationships: list[RelationshipInfo]


@dataclass
class CatalogValidation:
    table_count: int
    column_count: int
    physical_relationship_count: int
    virtual_relationship_count: int
    sensitive_column_count: int
    metric_count: int


def parse_mysql_ddl(path: Path) -> PhysicalCatalog:
    ddl = path.read_text(encoding="utf-8")
    tables: dict[str, PhysicalTable] = {}
    relationships: list[PhysicalRelationship] = []

    for table_match in CREATE_TABLE_PATTERN.finditer(ddl):
        table_name = table_match.group("name")
        body = re.sub(
            r"COMMENT\s*\n\s*'([^']*)'",
            lambda match: f"COMMENT '{match.group(1)}'",
            table_match.group("body"),
        )
        columns: dict[str, PhysicalColumn] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            column_match = COLUMN_PATTERN.match(line)
            if not column_match:
                continue
            rest = column_match.group("rest")
            comment_match = re.search(r"COMMENT\s+'([^']*)'", rest, re.IGNORECASE)
            comment = comment_match.group(1) if comment_match else ""
            enum_values: list[str] = []
            if comment.startswith("枚举："):
                enum_values = [value.strip() for value in comment[3:].split(",")]
            column = PhysicalColumn(
                name=column_match.group("name"),
                data_type=column_match.group("type"),
                nullable="NOT NULL" not in rest.upper(),
                primary_key="PRIMARY KEY" in rest.upper(),
                comment=comment,
                enum_values=enum_values,
            )
            columns[column.name] = column

        for relationship_match in FOREIGN_KEY_PATTERN.finditer(body):
            source_column = relationship_match.group("source_column")
            if source_column in columns:
                columns[source_column].foreign_key = True
            relationships.append(
                PhysicalRelationship(
                    name=relationship_match.group("name"),
                    source_table=table_name,
                    source_column=source_column,
                    target_table=relationship_match.group("target_table"),
                    target_column=relationship_match.group("target_column"),
                )
            )

        tables[table_name] = PhysicalTable(
            name=table_name,
            comment=table_match.group("comment"),
            columns=columns,
        )

    return PhysicalCatalog(tables=tables, relationships=relationships)


def load_meta_config(*paths: Path) -> MetaConfig:
    merged = MetaConfig(tables=[], metrics=[], polymorphic_relationships=[])
    for path in paths:
        content = OmegaConf.load(path)
        config: MetaConfig = OmegaConf.to_object(
            OmegaConf.merge(OmegaConf.structured(MetaConfig), content)
        )
        merged.domain = config.domain or merged.domain
        merged.tables.extend(config.tables or [])
        merged.metrics.extend(config.metrics or [])
        merged.polymorphic_relationships.extend(
            config.polymorphic_relationships or []
        )
        merged.sensitive_columns.extend(config.sensitive_columns)
    return merged


def humanize_column_name(name: str) -> str:
    if name in FIELD_LABELS:
        return FIELD_LABELS[name]
    labels = [TOKEN_LABELS.get(token, token) for token in name.split("_")]
    return "".join(labels)


def _column_role(column: PhysicalColumn) -> str:
    if column.primary_key:
        return "primary_key"
    if column.foreign_key:
        return "foreign_key"
    measure_tokens = (
        "amount",
        "count",
        "duration",
        "seconds",
        "score",
        "quantity",
        "balance",
        "price",
        "rank_no",
    )
    if any(token in column.name for token in measure_tokens):
        return "measure"
    return "dimension"


def _overrides_by_table(config: MetaConfig) -> dict[str, dict[str, ColumnConfig]]:
    return {
        table.name: {column.name: column for column in table.columns}
        for table in config.tables or []
    }


def build_domain_metadata(
    physical: PhysicalCatalog,
    config: MetaConfig,
) -> DomainMetadata:
    table_configs = {table.name: table for table in config.tables or []}
    overrides = _overrides_by_table(config)
    sensitive_columns = set(config.sensitive_columns)
    table_infos: list[TableInfo] = []
    column_infos: list[ColumnInfo] = []

    for table_name, physical_table in physical.tables.items():
        table_config = table_configs[table_name]
        table_infos.append(
            TableInfo(
                id=table_name,
                name=table_name,
                role=table_config.role,
                description=table_config.description or physical_table.comment,
                domain=table_config.domain or config.domain,
                alias=table_config.alias,
            )
        )
        for column in physical_table.columns.values():
            override = overrides.get(table_name, {}).get(column.name)
            label = humanize_column_name(column.name)
            column_id = f"{table_name}.{column.name}"
            description = (
                override.description
                if override and override.description
                else f"{table_config.description}中的{label}字段。"
            )
            aliases = override.alias if override and override.alias else [label]
            sensitive = column_id in sensitive_columns or bool(
                override and override.sensitive
            )
            column_infos.append(
                ColumnInfo(
                    id=column_id,
                    name=column.name,
                    type=column.data_type,
                    role=(override.role if override and override.role else _column_role(column)),
                    examples=[],
                    description=description,
                    alias=aliases,
                    table_id=table_name,
                    nullable=column.nullable,
                    sensitive=sensitive,
                    sync=bool(override and override.sync and not sensitive),
                    enum_values=column.enum_values,
                )
            )

    relationship_infos = [
        RelationshipInfo(
            id=relationship.name,
            source_table=relationship.source_table,
            source_column=relationship.source_column,
            target_table=relationship.target_table,
            target_column=relationship.target_column,
            physical=True,
        )
        for relationship in physical.relationships
    ]
    for polymorphic in config.polymorphic_relationships or []:
        for discriminator_value, target_table in polymorphic.targets.items():
            relationship_infos.append(
                RelationshipInfo(
                    id=f"{polymorphic.id}.{discriminator_value}",
                    source_table=polymorphic.source_table,
                    source_column=polymorphic.source_column,
                    target_table=target_table,
                    target_column=polymorphic.target_column,
                    relationship_type=polymorphic.relationship_type,
                    condition=(
                        f"{polymorphic.source_table}.{polymorphic.discriminator_column} "
                        f"= '{discriminator_value}'"
                    ),
                    physical=False,
                )
            )

    return DomainMetadata(
        tables=table_infos,
        columns=column_infos,
        relationships=relationship_infos,
    )


def validate_domain_catalog(
    physical: PhysicalCatalog,
    config: MetaConfig,
    *,
    expected_table_count: int = 54,
) -> CatalogValidation:
    physical_tables = set(physical.tables)
    configured_tables = {table.name for table in config.tables or []}
    if len(physical_tables) != expected_table_count:
        raise ValueError(
            f"expected {expected_table_count} physical tables, got {len(physical_tables)}"
        )
    if missing := physical_tables - configured_tables:
        raise ValueError(f"missing table semantics: {', '.join(sorted(missing))}")
    if unknown := configured_tables - physical_tables:
        raise ValueError(f"unknown semantic tables: {', '.join(sorted(unknown))}")

    for table in config.tables or []:
        physical_columns = physical.tables[table.name].columns
        for column in table.columns:
            if column.name not in physical_columns:
                raise ValueError(f"unknown column override: {table.name}.{column.name}")

    all_column_ids = {
        f"{table.name}.{column.name}"
        for table in physical.tables.values()
        for column in table.columns.values()
    }
    if unknown_sensitive := set(config.sensitive_columns) - all_column_ids:
        raise ValueError(
            f"unknown sensitive columns: {', '.join(sorted(unknown_sensitive))}"
        )

    virtual_count = 0
    for relationship in config.polymorphic_relationships or []:
        source = physical.tables.get(relationship.source_table)
        if source is None:
            raise ValueError(f"unknown relationship source: {relationship.source_table}")
        for column_name in (
            relationship.source_column,
            relationship.discriminator_column,
        ):
            if column_name not in source.columns:
                raise ValueError(
                    f"unknown relationship column: {relationship.source_table}.{column_name}"
                )
        for target_table in relationship.targets.values():
            target = physical.tables.get(target_table)
            if target is None or relationship.target_column not in target.columns:
                raise ValueError(
                    f"unknown relationship target: {target_table}.{relationship.target_column}"
                )
            virtual_count += 1

    domain_metadata = build_domain_metadata(physical, config)
    if any(column.sensitive and column.sync for column in domain_metadata.columns):
        raise ValueError("sensitive columns must not be synchronized to search indexes")

    metrics = config.metrics or []
    metric_names = [metric.name for metric in metrics]
    if len(metric_names) != len(set(metric_names)):
        raise ValueError("metric names must be unique")
    for metric in metrics:
        if not metric.formula.strip():
            raise ValueError(f"metric {metric.name} has no formula")
        referenced_columns = set(metric.relevant_columns) | set(metric.dimensions)
        if metric.time_column:
            referenced_columns.add(metric.time_column)
        if metric.currency_column:
            referenced_columns.add(metric.currency_column)
        if unknown_columns := referenced_columns - all_column_ids:
            raise ValueError(
                f"metric {metric.name} references unknown columns: "
                f"{', '.join(sorted(unknown_columns))}"
            )
        if sensitive_references := referenced_columns & set(config.sensitive_columns):
            raise ValueError(
                f"metric {metric.name} references sensitive columns: "
                f"{', '.join(sorted(sensitive_references))}"
            )
        if metric.unit == "currency" and not metric.currency_column:
            raise ValueError(f"currency metric {metric.name} has no currency_column")

    return CatalogValidation(
        table_count=len(physical.tables),
        column_count=len(all_column_ids),
        physical_relationship_count=len(physical.relationships),
        virtual_relationship_count=virtual_count,
        sensitive_column_count=sum(
            1 for column in domain_metadata.columns if column.sensitive
        ),
        metric_count=len(metrics),
    )
