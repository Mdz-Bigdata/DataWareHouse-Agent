"""命令行装配层：把散落在各子系统里的共享契约收口成四条可直接跑的命令。

    ddl-export        渲染四层建表脚本 + Paimon catalog 初始化脚本到 ddl/
    catalog-validate  全表硬校验 + 命名偏离审计（缺域段 / 缺后缀 两类分别计数）
    catalog-stats     数据域 × 层级 表数统计，并与原文口径对账（伪域单列）
    id-demo           三级 ID 体系的生成 / 派生 / 反解演示

三条设计约束：

1. **零副作用读取**：除 ``ddl-export`` 外的子命令只读 ``catalog.registry``，不碰文件系统、
   不连任何外部组件，因此可以在 CI 里当断言跑。
2. **连接参数一律取自 config.settings()**：DDL 里的 catalog 名、database 名、warehouse
   路径不写死，环境变量覆盖后重新导出即可切环境。
3. **口令不落盘**：生成的 SQL 里 secret 只写 ``${MINIO_SECRET_KEY}`` 占位符，
   与 ``adas_lakehouse.ingest.sql`` 的既有约定保持一致。

用法::

    python -m adas_lakehouse.cli catalog-stats
    python -m adas_lakehouse.cli ddl-export --layer dwd --out /tmp/ddl
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from . import __version__, ids, naming
from .catalog import registry
from .catalog.spec import TableSpec
from .config import Settings, settings
from .domains import QUALITY_GATE_PSEUDO_DOMAIN, DataDomain, Layer

__all__ = [
    "LAYER_FILES",
    "CATALOG_FILE",
    "repo_root",
    "render_catalog_script",
    "render_layer_script",
    "export_ddl",
    "main",
]

# --------------------------------------------------------------------------- 产物布局

#: 四层建表脚本文件名。数字前缀保证按层顺序执行：ODS 先于 DWD，依此类推。
LAYER_FILES: dict[Layer, str] = {
    Layer.ODS: "10_ods.sql",
    Layer.DWD: "20_dwd.sql",
    Layer.DWS: "30_dws.sql",
    Layer.ADS: "40_ads.sql",
}

#: catalog / database 初始化，必须最先执行（四层脚本用的是三段式全限定表名）。
CATALOG_FILE = "00_catalog.sql"

_REGEN_HINT = "python3 scripts/export_ddl.py"

# --------------------------------------------------------------------------- 原文对账口径
#
# 以下数字全部来自 catalog/registry.py 模块 docstring 已登记的原文口径，
# 本模块只负责把它们摆出来跟实际注册表对账，不做任何改写。
# 逐域表数原文未给出（11 数据域表只给了合计与分层数），因此对账在「分层」粒度做。

#: 原文显式表名清单的分层表数（含质量门禁伪域 1 张，计在 ODS）。
SOURCE_EXPLICIT_COUNTS: dict[Layer, int] = {
    Layer.ODS: 32,
    Layer.DWD: 30,
    Layer.DWS: 14,
    Layer.ADS: 11,
}

#: 原文正文概述口径：「79+ 张」，其中 ODS 28 / DWD 27。与显式清单本身即不自洽。
SOURCE_PROSE_TOTAL_TEXT = "79+"
SOURCE_PROSE_ODS = 28
SOURCE_PROSE_DWD = 27

#: 另一篇 11 数据域统计表口径：「合计 87+ 张（含质量门禁 1 张）」。
SOURCE_DOMAIN_TABLE_TOTAL_TEXT = "87+"
SOURCE_DOMAIN_TABLE_GATE_COUNT = 1

#: 本项目在原文显式清单之外补登记的表：原文只在分区全景表里出现过它，正文清单漏列。
PROJECT_ADDED_TABLES: dict[str, str] = {
    "ods_production_kafka_event": "仅在原文分区策略全景表中出现，正文表名清单漏列",
}

# --------------------------------------------------------------------------- 排版工具


def _w(text: str) -> int:
    """终端显示宽度：中日韩全角字符按 2 列算，否则表格对不齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int, *, right: bool = False) -> str:
    gap = max(0, width - _w(text))
    return (" " * gap + text) if right else (text + " " * gap)


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    numeric_from: int = 1,
    align: str = "",
) -> str:
    """渲染一张等宽对齐的文本表格。

    Args:
        numeric_from: 从第几列起右对齐（数字列右对齐才读得出量级差异）。
        align: 逐列对齐串，如 ``"lrrl"``；给了就覆盖 numeric_from。
    """
    cols = len(headers)
    spec = align or "".join("l" if i < numeric_from else "r" for i in range(cols))
    widths = [
        max(_w(headers[i]), *(_w(r[i]) for r in rows)) if rows else _w(headers[i])
        for i in range(cols)
    ]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            _pad(cells[i], widths[i], right=spec[i] == "r") for i in range(cols)
        ).rstrip()

    out = [line(headers), "  ".join("-" * widths[i] for i in range(cols))]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def _rule(title: str = "", width: int = 74) -> str:
    if not title:
        return "=" * width
    return f"== {title} " + "=" * max(0, width - _w(title) - 4)


def repo_root() -> Path:
    """仓库根目录：src/adas_lakehouse/cli.py → 上溯两级。"""
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- DDL 渲染


def _sql_header(title: str, notes: Iterable[str]) -> str:
    line = "-- " + "=" * 72
    body = "\n".join(f"-- {n}" for n in notes)
    return f"{line}\n-- {title}\n{body}\n{line}\n"


def render_catalog_script(cfg: Settings | None = None) -> str:
    """``ddl/00_catalog.sql``：Paimon Catalog + database 创建语句。

    参数全部取自 ``config.settings()``（MinIO 三段 + Paimon 三段）。
    secret 写占位符——生成物要能直接进 Git，口令由部署时的环境变量注入。
    """
    cfg = cfg or settings()
    minio, paimon = cfg.minio, cfg.paimon
    opts = {
        "type": "paimon",
        "warehouse": minio.warehouse_path,
        "metastore": paimon.metastore,
        "s3.endpoint": minio.endpoint,
        "s3.access-key": minio.access_key,
        # 口令不落盘：部署时用 Flink secret 机制或环境变量注入
        "s3.secret-key": "${MINIO_SECRET_KEY}",
        "s3.path.style.access": "true",
    }
    opt_sql = ",\n".join(f"  '{k}' = '{v}'" for k, v in opts.items())
    return (
        _sql_header(
            "湖仓建表 · 第 0 步：Paimon Catalog 与 Database",
            [
                f"由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：{_REGEN_HINT}",
                "连接参数取自 config.settings()：MINIO_* / PAIMON_* 环境变量可覆盖。",
                "执行顺序：00_catalog → 10_ods → 20_dwd → 30_dws → 40_ads。",
                "10~40 用的是 `catalog`.`database`.`table` 全限定名，本脚本必须先跑。",
            ],
        )
        + f"\nCREATE CATALOG IF NOT EXISTS `{paimon.catalog}` WITH (\n{opt_sql}\n);\n"
        + f"\nUSE CATALOG `{paimon.catalog}`;\n"
        + f"CREATE DATABASE IF NOT EXISTS `{paimon.database}`;\n"
        + f"USE `{paimon.database}`;\n"
    )


def _layer_domain_rows(tables: Sequence[TableSpec]) -> list[str]:
    """层内按数据域分组的一行行注释，读脚本的人一眼看到这层都覆盖了哪些域。"""
    grouped: dict[tuple[int, str], list[str]] = {}
    for t in tables:
        # 伪域排在 11 数据域之后，读脚本时一眼看出「哪些不属于数据域」
        key = (
            (99, f"[伪域]{t.pseudo_domain.rstrip('_')}")
            if t.pseudo_domain
            else (
                t.domain.ordinal,
                t.domain.name_cn,
            )
        )
        grouped.setdefault(key, []).append(t.name)
    return [f"  · {k[1]}: {len(v)} 张" for k, v in sorted(grouped.items())]


def render_layer_script(layer: Layer, cfg: Settings | None = None) -> str:
    """某一层的全部建表语句。字段与物理策略全部来自 catalog.registry 的 TableSpec。"""
    cfg = cfg or settings()
    tables = registry.by_layer(layer)
    notes = [
        f"由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：{_REGEN_HINT}",
        f"层级定位：{layer.purpose}",
        f"系统字段：{' + '.join(layer.system_fields)}",
        f"本层共 {len(tables)} 张表：",
        *_layer_domain_rows(tables),
        "物理策略（分区 / bucket / changelog-producer）由 catalog/spec.py 硬校验后渲染。",
    ]
    body = "\n".join(
        t.render_ddl(catalog=cfg.paimon.catalog, database=cfg.paimon.database) for t in tables
    )
    return _sql_header(f"湖仓建表 · {layer.value.upper()} 层", notes) + "\n" + body


def _count_create_table(sql: str) -> int:
    return sql.count("CREATE TABLE IF NOT EXISTS")


def export_ddl(
    out_dir: Path | str | None = None,
    layer: Layer | None = None,
    cfg: Settings | None = None,
) -> list[tuple[Path, int]]:
    """导出建表脚本。

    Args:
        out_dir: 输出目录，缺省 ``<repo>/ddl``。
        layer: 只导某一层；缺省导全部四层并附带 00_catalog.sql。
        cfg: 连接配置，缺省 ``config.settings()``。

    Returns:
        ``[(文件路径, 该文件的建表语句数), ...]``。catalog 脚本的建表语句数为 0。
    """
    cfg = cfg or settings()
    target = Path(out_dir) if out_dir else repo_root() / "ddl"
    target.mkdir(parents=True, exist_ok=True)

    written: list[tuple[Path, int]] = []
    if layer is None:
        path = target / CATALOG_FILE
        sql = render_catalog_script(cfg)
        path.write_text(sql, encoding="utf-8")
        written.append((path, _count_create_table(sql)))

    for lyr in [layer] if layer else list(Layer):
        path = target / LAYER_FILES[lyr]
        sql = render_layer_script(lyr, cfg)
        path.write_text(sql, encoding="utf-8")
        written.append((path, _count_create_table(sql)))
    return written


# --------------------------------------------------------------------------- 命名审计


def _audit_naming() -> dict[str, object]:
    """命名偏离审计：把 88 张表按「缺域段 / 缺后缀」两类分别归档。

    两类偏离的性质不同，必须分开计数：
      · 缺域段：表名第二段解析不出数据域（ods_vehicle_info、ads_hard_case_library），
        域归属只能靠 registry 显式声明兜底；
      · 缺后缀：表名第四段不是 13 种标准粒度后缀之一（原文标题写「10 种」，
        表里 3 行各含两个后缀，逐个数 13 个，见 source-deviations A-7）。ODS/ADS 层本就不强制，
        因此只有 DWD/DWS 层的缺后缀才是 lint 告警。
    """
    missing_domain: list[TableSpec] = []
    missing_suffix: list[TableSpec] = []
    alias_domain: list[TableSpec] = []
    canonical: list[TableSpec] = []
    unparsable: list[tuple[str, str]] = []

    for t in registry.all_tables():
        try:
            p = naming.parse(t.name)
        except ValueError as exc:  # pragma: no cover - registry 已硬校验过
            unparsable.append((t.name, str(exc)))
            continue
        if p.domain is None:
            missing_domain.append(t)
        elif not t.name.startswith(f"{t.layer.value}_{t.domain.prefix}"):
            # 域段靠 naming.DOMAIN_ALIASES 才认出来（badcase_ / ota_ / qc_ ...）
            alias_domain.append(t)
        if p.suffix is None:
            missing_suffix.append(t)
        if p.is_canonical:
            canonical.append(t)

    warned_suffix = [t for t in missing_suffix if t.layer in (Layer.DWD, Layer.DWS)]
    return {
        "missing_domain": missing_domain,
        "missing_domain_declared": [t for t in missing_domain if t.name_omits_domain],
        "missing_domain_undeclared": [t for t in missing_domain if not t.name_omits_domain],
        "missing_suffix": missing_suffix,
        "missing_suffix_warned": warned_suffix,
        "missing_suffix_declared": [t for t in warned_suffix if t.name_omits_suffix],
        "missing_suffix_undeclared": [t for t in warned_suffix if not t.name_omits_suffix],
        "alias_domain": alias_domain,
        "canonical": canonical,
        "unparsable": unparsable,
    }


def _layer_histogram(tables: Sequence[TableSpec]) -> str:
    counts = {lyr: sum(1 for t in tables if t.layer is lyr) for lyr in Layer}
    return " / ".join(f"{lyr.value} {counts[lyr]}" for lyr in Layer)


def _name_list(tables: Sequence[TableSpec], *, indent: str = "       ") -> str:
    return "\n".join(f"{indent}{t.name}" for t in tables)


# --------------------------------------------------------------------------- 子命令


def cmd_ddl_export(args: argparse.Namespace) -> int:
    layer = Layer(args.layer) if args.layer else None
    cfg = settings()
    written = export_ddl(out_dir=args.out, layer=layer, cfg=cfg)

    print(_rule("ddl-export"))
    print(f"Paimon catalog : {cfg.paimon.catalog}")
    print(f"Database       : {cfg.paimon.database}")
    print(f"Warehouse      : {cfg.minio.warehouse_path}  (metastore={cfg.paimon.metastore})")
    print()
    rows = [
        [p.name, str(n), str(len(p.read_text(encoding="utf-8").splitlines()))] for p, n in written
    ]
    print(_table(["文件", "建表语句", "行数"], rows))
    total = sum(n for _, n in written)
    print()
    print(f"输出目录：{written[0][0].parent}")
    if layer is None:
        expect = len(registry.all_tables())
        ok = total == expect
        print(f"建表语句合计：{total} / 注册表 {expect} 张 {'✅ 一致' if ok else '❌ 不一致'}")
        return 0 if ok else 1
    print(f"建表语句合计：{total}（仅 {layer.value.upper()} 层；未生成 {CATALOG_FILE}）")
    return 0


def cmd_catalog_validate(args: argparse.Namespace) -> int:
    tables = registry.all_tables()
    problems = registry.validate_all()

    print(_rule("catalog-validate"))
    print(f"[1/2] 硬校验 registry.validate_all()：{len(tables)} 张表")
    if problems:
        for name, items in problems.items():
            print(f"  ❌ {name}")
            for it in items:
                print(f"       - {it}")
        print(f"  合计 {len(problems)} 张表存在 {sum(len(v) for v in problems.values())} 处违规")
    else:
        print("  ✅ 0 违规（命名 / 主键 / 分区含主键 / bucket 五档 / changelog 分层 全通过）")

    a = _audit_naming()
    md, ms = a["missing_domain"], a["missing_suffix"]
    ms_warn = a["missing_suffix_warned"]
    print()
    print(
        f"[2/2] 命名偏离审计（naming.lint，非阻断；四段式完全合规 {len(a['canonical'])}/{len(tables)} 张）"
    )
    print()
    print(f"  A. 缺域段（第二段解析不出数据域）：{len(md)} 张   [{_layer_histogram(md)}]")
    print(
        f"       已在 TableSpec 登记 name_omits_domain=True : {len(a['missing_domain_declared'])}"
    )
    print(
        f"       未登记（需补登记或改名）                   : {len(a['missing_domain_undeclared'])}"
    )
    if args.list:
        print(_name_list(md))
    elif a["missing_domain_undeclared"]:
        print(_name_list(a["missing_domain_undeclared"]))

    print()
    print(
        f"  B. 缺后缀（第四段不在 naming.GRANULARITY_SUFFIXES 的 "
        f"{len(naming.GRANULARITY_SUFFIXES)} 种标准粒度后缀内）：{len(ms)} 张   [{_layer_histogram(ms)}]"
    )
    print(f"       其中构成 lint 告警的（DWD/DWS 层强制）     : {len(ms_warn)}")
    print(
        f"       已在 TableSpec 登记 name_omits_suffix=True : {len(a['missing_suffix_declared'])}"
    )
    print(
        f"       未登记（需补登记或改名）                   : {len(a['missing_suffix_undeclared'])}"
    )
    print("       注：ODS/ADS 层不强制粒度后缀——ODS 贴源命名、ADS 面向应用命名。")
    if args.list:
        print(_name_list(ms))
    elif a["missing_suffix_undeclared"]:
        print(_name_list(a["missing_suffix_undeclared"]))

    print()
    print(f"  C. 域段靠别名解析（naming.DOMAIN_ALIASES）：{len(a['alias_domain'])} 张")
    print("       原文把评测域写成 badcase_、部署域写成 ota_ 等；域归属以 registry 显式声明为准。")
    if args.list:
        print(_name_list(a["alias_domain"]))

    undeclared = len(a["missing_domain_undeclared"]) + len(a["missing_suffix_undeclared"])
    print()
    print(
        f"结论：硬违规 {len(problems)} 处；命名偏离 {len(md) + len(ms_warn)} 处"
        f"（已登记 {len(a['missing_domain_declared']) + len(a['missing_suffix_declared'])}，未登记 {undeclared}）"
    )
    return 0 if not problems and not undeclared else 1


def cmd_catalog_stats(args: argparse.Namespace) -> int:
    tables = registry.all_tables()
    layers = list(Layer)

    def cell(domain_key: str, lyr: Layer) -> int:
        return sum(
            1
            for t in tables
            if t.layer is lyr and not t.pseudo_domain and t.domain.key == domain_key
        )

    print(_rule("catalog-stats"))
    print("一、数据域 × 层级 表数（来源：catalog.registry.all_tables()）")
    print()
    rows: list[list[str]] = []
    for d in sorted(DataDomain, key=lambda x: x.ordinal):
        per = [cell(d.key, lyr) for lyr in layers]
        rows.append(
            [f"{d.ordinal:02d}", d.name_cn, d.prefix, *[str(x) for x in per], str(sum(per))]
        )
    domain_totals = [sum(cell(d.key, lyr) for d in DataDomain) for lyr in layers]
    rows.append(
        ["--", "11 数据域小计", "", *[str(x) for x in domain_totals], str(sum(domain_totals))]
    )

    pseudo = [t for t in tables if t.pseudo_domain]
    pseudo_per = [sum(1 for t in pseudo if t.layer is lyr) for lyr in layers]
    rows.append(
        [
            "--",
            f"[伪域] {QUALITY_GATE_PSEUDO_DOMAIN.rstrip('_')}",
            QUALITY_GATE_PSEUDO_DOMAIN,
            *[str(x) for x in pseudo_per],
            str(len(pseudo)),
        ]
    )
    grand = [sum(1 for t in tables if t.layer is lyr) for lyr in layers]
    rows.append(["--", "全湖合计", "", *[str(x) for x in grand], str(len(tables))])
    print(
        _table(
            ["#", "数据域", "域前缀", *[layer.value.upper() for layer in layers], "小计"],
            rows,
            numeric_from=3,
        )
    )
    print()
    print("  伪域说明：ods_quality_issue 是入湖闸门的异常隔离表，不属于 11 数据域，故单列——")
    print("            这样 11 数据域小计才能直接跟原文统计表对账。")
    print("  闭环域无 ODS 表：它是跨域整合域，数据全部来自其他域 DWD 层加工。")

    print()
    print("二、与原文口径对账（原文数字见 catalog/registry.py 模块 docstring）")
    print()
    recon: list[list[str]] = []
    for lyr in layers:
        src = SOURCE_EXPLICIT_COUNTS[lyr]
        got = sum(1 for t in tables if t.layer is lyr)
        diff = got - src
        recon.append([lyr.value.upper(), str(src), str(got), f"{diff:+d}" if diff else "0"])
    src_total = sum(SOURCE_EXPLICIT_COUNTS.values())
    recon.append(["合计", str(src_total), str(len(tables)), f"{len(tables) - src_total:+d}"])
    print(_table(["分层", "原文显式表名清单", "本项目注册表", "差异"], recon))
    print()
    for name, why in PROJECT_ADDED_TABLES.items():
        spec = registry.by_name(name)
        print(f"  差异来源：+1 {name}（{spec.layer.value.upper()} / {spec.domain.name_cn}）— {why}")
    print()
    print("  原文三处口径互不自洽，本项目以「显式表名清单」为准：")
    print(
        f"    · 正文概述        ：{SOURCE_PROSE_TOTAL_TEXT} 张"
        f"（ODS {SOURCE_PROSE_ODS} / DWD {SOURCE_PROSE_DWD}）"
        f" —— 比显式清单少 {SOURCE_EXPLICIT_COUNTS[Layer.ODS] - SOURCE_PROSE_ODS} + "
        f"{SOURCE_EXPLICIT_COUNTS[Layer.DWD] - SOURCE_PROSE_DWD} 张"
    )
    print(
        f"    · 显式表名清单    ：{src_total} 张"
        f"（ODS {SOURCE_EXPLICIT_COUNTS[Layer.ODS]} / DWD {SOURCE_EXPLICIT_COUNTS[Layer.DWD]}"
        f" / DWS {SOURCE_EXPLICIT_COUNTS[Layer.DWS]} / ADS {SOURCE_EXPLICIT_COUNTS[Layer.ADS]}）"
    )
    print(
        f"    · 11 数据域统计表 ：{SOURCE_DOMAIN_TABLE_TOTAL_TEXT} 张"
        f"（含质量门禁 {SOURCE_DOMAIN_TABLE_GATE_COUNT} 张）—— 与显式清单一致"
    )
    print(
        f"    · 本项目          ：{len(tables)} 张 = {src_total} 张显式清单"
        f" + {len(PROJECT_ADDED_TABLES)} 张补登记；其中 11 数据域 {sum(domain_totals)} 张、"
        f"伪域 {len(pseudo)} 张"
    )
    print()
    print("  ⚠️ 原文未明确，本项目设计：原文 11 数据域表只给出合计与分层数，未逐域给出表数，")
    print("     故上表逐域明细为本项目登记口径，对账在「分层」粒度成立。")

    print()
    print("三、物理策略分布（catalog/spec.py 硬校验口径）")
    print()
    parted = [t for t in tables if t.partition_by]
    bucket_rows = []
    for b in sorted({t.bucket for t in tables}):
        names = [t for t in tables if t.bucket == b]
        bucket_rows.append([str(b), str(len(names)), _layer_histogram(names)])
    print(_table(["bucket", "表数", "分层分布"], bucket_rows, align="rrl"))
    print()
    print(f"  分区表 {len(parted)} 张（其余走「Upsert 无维度则不分区」）：")
    for t in parted:
        print(f"    · {t.name:<42s} PARTITIONED BY ({', '.join(t.partition_by)})")
    cl_rows = []
    for cp in sorted({t.changelog_producer.value for t in tables}):
        names = [t for t in tables if t.changelog_producer.value == cp]
        cl_rows.append([cp, str(len(names)), _layer_histogram(names)])
    print()
    print(_table(["changelog-producer", "表数", "分层分布"], cl_rows, align="lrl"))
    return 0


def cmd_id_demo(args: argparse.Namespace) -> int:
    moment = datetime(2024, 1, 15, 14, 30, 22)
    print(_rule("id-demo · 三级 ID 体系"))
    print("data_id（clip 终身锚点） → artifact_id（处理产物） → run_id（一次执行）")
    print()

    did = ids.new_data_id(args.vehicle, moment)
    back = ids.parse_data_id(str(did))
    print("一级 · data_id —— 采集端生成，随数据文件上传，重刷不变")
    print(f"  生成 : {did}")
    print(
        f"  反解 : 车辆={back.vehicle_code}  采集时刻={back.collected_at:%Y-%m-%d %H:%M:%S}"
        f"  序列={back.sequence}（规则一：取自 UUID，保证唯一）"
    )
    print(f"  规则二：时间戳 {ids.TS_FORMAT} 精确到秒，按字典序排序即还原生产顺序")

    print()
    print("二级 · artifact_id —— 由「输入 data_id + 环节 + 算法版本 + 内容哈希」派生")
    payload = b"annotated-bboxes-v3"
    a_v3 = ids.derive_artifact_id(did, "annotation", "v3", payload)
    a_v3_again = ids.derive_artifact_id(did, "annotation", "v3", payload)
    a_v4 = ids.derive_artifact_id(did, "annotation", "v4", b"annotated-bboxes-v4")
    print(f"  标注产物 v3 : {a_v3}")
    print(f"  同输入重跑  : {a_v3_again}")
    print(
        f"  → 幂等: {'一致 ✅ 重试不产生重复产物' if a_v3.raw == a_v3_again.raw else '不一致 ❌'}"
        "（规则一：content_hash 相同则 ID 相同）"
    )
    print(f"  算法升级 v4 : {a_v4}")
    print(
        f"  → 规则三：data_id 不变（{a_v4.data_id == a_v3.data_id}），新产物另起 ID，"
        f"旧产物保留并标记 {ids.ArtifactStatus.SUPERSEDED.value}，v3/v4 效果可并行对比"
    )
    p = ids.parse_artifact_id(str(a_v4))
    print(
        f"  反解 : 锚点={p.data_id}  环节={p.stage}  算法版本={p.algo_version}"
        f"  内容哈希={p.content_hash}"
    )

    print()
    print("  规则四 · 逐级派生链（湖仓冗余 parent_artifact_id + 图库 DERIVED_FROM 边）")
    frame = ids.derive_artifact_id(did, "keyframe", "v2", b"frame-00042.jpg")
    vector = ids.derive_artifact_id(did, "embedding", "v1.2", b"<768-dim float32>")
    for label, art in (("clip 抽帧", frame), ("帧向量化", vector)):
        print(f"    {_pad(label, 12)} {art}")
    print(f"    ↑ 两者的锚点同为 {frame.data_id}，任何产物都能逐级回溯到源头 clip")

    print()
    print("三级 · run_id —— 一次执行一条，绑定算法版本与参数快照")
    rid = ids.new_run_id("annotation", datetime(2024, 1, 15, 15, 0, 0))
    rb = ids.parse_run_id(str(rid))
    print(f"  生成 : {rid}")
    print(
        f"  反解 : 环节={rb.stage}  启动时刻={rb.started_at:%Y-%m-%d %H:%M:%S}  序列={rb.sequence}"
    )
    print("  同一 artifact_id 可对应多条 run_id（重试/回刷），排障时用 run_id 定位具体那一次执行")

    print()
    print("反例演示（早失败，产物必须挂在合法锚点上）")
    for bad in ("COLLECT_BP_2024_0001", "not-an-id"):
        try:
            ids.parse_data_id(bad)
        except ValueError as exc:
            print(f"  parse_data_id({bad!r}) → ValueError: {exc}")
    return 0


# --------------------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adas-lakehouse",
        description="智驾数据闭环湖仓：88 张 Paimon 表的建表导出、契约校验与 ID 体系演示。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  adas-lakehouse ddl-export                  # 导出 00_catalog + 四层建表脚本到 ddl/\n"
            "  adas-lakehouse ddl-export --layer dwd      # 只导 DWD 层\n"
            "  adas-lakehouse catalog-validate --list     # 校验并列出全部偏离表名\n"
            "  adas-lakehouse catalog-stats               # 数据域 × 层级 统计 + 原文对账\n"
            "  adas-lakehouse id-demo --vehicle BP        # 三级 ID 生成/派生/反解\n"
        ),
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"adas-lakehouse {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_ddl = sub.add_parser(
        "ddl-export",
        help="渲染建表脚本到 ddl/（00_catalog.sql + 10_ods/20_dwd/30_dws/40_ads.sql）",
        description="按层渲染 Paimon 建表语句。字段、主键、分区、bucket、changelog-producer "
        "全部取自 catalog.registry，连接参数取自 config.settings()。",
    )
    p_ddl.add_argument(
        "--layer",
        choices=[layer.value for layer in Layer],
        help="只导出指定层；缺省导出四层并附带 00_catalog.sql",
    )
    p_ddl.add_argument("--out", metavar="DIR", help="输出目录，缺省 <repo>/ddl")
    p_ddl.set_defaults(func=cmd_ddl_export)

    p_val = sub.add_parser(
        "catalog-validate",
        help="全表硬校验 + 命名偏离审计（缺域段 / 缺后缀 分类计数）",
        description="硬校验覆盖：命名合法性、主键三原则、分区表主键含分区字段、bucket 五档、"
        "changelog-producer 分层默认、ODS 必须声明 source_system。",
    )
    p_val.add_argument("--list", action="store_true", help="列出每一类偏离的完整表名清单")
    p_val.set_defaults(func=cmd_catalog_validate)

    p_stats = sub.add_parser(
        "catalog-stats",
        help="数据域 × 层级 表数统计，与原文 11 数据域表对账（伪域单列）",
    )
    p_stats.set_defaults(func=cmd_catalog_stats)

    p_id = sub.add_parser("id-demo", help="三级 ID 体系：生成 / 派生 / 反解演示")
    p_id.add_argument("--vehicle", default="BP", metavar="CODE", help="车辆编码，缺省 BP")
    p_id.set_defaults(func=cmd_id_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
