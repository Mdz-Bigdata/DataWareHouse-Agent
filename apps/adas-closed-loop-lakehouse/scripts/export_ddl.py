#!/usr/bin/env python3
"""把 catalog.registry 里的 88 张表规格渲染成可直接执行的建表脚本。

产物（缺省落在 ``<repo>/ddl/``）::

    00_catalog.sql   Paimon Catalog + Database 创建语句（参数取自 config.settings()）
    10_ods.sql       ODS 层建表语句
    20_dwd.sql       DWD 层建表语句
    30_dws.sql       DWS 层建表语句
    40_ads.sql       ADS 层建表语句

数字前缀即执行顺序：``00 → 10 → 20 → 30 → 40``。

为什么是「生成」而不是「手写」：88 张表的分区字段、bucket 档位、changelog-producer
在 ``catalog/spec.py`` 里已有硬校验，手写 DDL 等于把这套约束复制一遍、并从此开始漂移。
生成脚本保证 SQL 与 Python 契约永远同源——改 TableSpec，重跑本脚本即可。

用法::

    python3 scripts/export_ddl.py                    # 全量导出并自检
    python3 scripts/export_ddl.py --layer dwd        # 只导 DWD 层
    python3 scripts/export_ddl.py --out /tmp/ddl     # 导到别处（不污染仓库）
    python3 scripts/export_ddl.py --check            # 只校验不写盘，供 CI 用

自检：四个分层文件里的建表语句总数必须等于 ``registry.all_tables()`` 的表数（88），
对不上直接退出码 1。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 未安装成包时也能直接 `python3 scripts/export_ddl.py` 跑起来
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adas_lakehouse import cli  # noqa: E402
from adas_lakehouse.catalog import registry  # noqa: E402
from adas_lakehouse.config import settings  # noqa: E402
from adas_lakehouse.domains import Layer  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="export_ddl.py",
        description="渲染四层建表脚本 + Paimon catalog 初始化脚本到 ddl/",
    )
    p.add_argument("--layer", choices=[layer.value for layer in Layer], help="只导出指定层")
    p.add_argument("--out", metavar="DIR", help="输出目录，缺省 <repo>/ddl")
    p.add_argument("--check", action="store_true", help="只做契约校验与计数自检，不写盘")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = settings()
    tables = registry.all_tables()

    # 先校验后落盘：契约不干净就不该产出 SQL，否则脏定义会被执行到生产
    problems = registry.validate_all()
    if problems:
        print(f"❌ 契约校验未通过，{len(problems)} 张表存在违规，已中止导出：", file=sys.stderr)
        for name, items in problems.items():
            for it in items:
                print(f"   {name}: {it}", file=sys.stderr)
        return 1
    print(f"✅ 契约校验通过：{len(tables)} 张表，0 违规")

    if args.check:
        print("--check：仅校验，未写盘")
        return 0

    layer = Layer(args.layer) if args.layer else None
    written = cli.export_ddl(out_dir=args.out, layer=layer, cfg=cfg)
    out_dir = written[0][0].parent

    print(
        f"Paimon: catalog={cfg.paimon.catalog} database={cfg.paimon.database} "
        f"warehouse={cfg.minio.warehouse_path}"
    )
    print(f"输出目录: {out_dir}")
    total = 0
    for path, n in written:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        total += n
        tag = f"{n:>3d} 条建表语句" if n else "     catalog/database 初始化"
        print(f"  {path.name:<16s} {tag}   {lines:>5d} 行")

    if layer is not None:
        print(f"只导出了 {layer.value.upper()} 层，跳过总数自检")
        return 0

    expect = len(tables)
    if total != expect:
        print(f"❌ 自检失败：四层建表语句共 {total} 条，注册表 {expect} 张", file=sys.stderr)
        return 1
    print(f"✅ 自检通过：四层建表语句合计 {total} 条 == registry.all_tables() 的 {expect} 张表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
