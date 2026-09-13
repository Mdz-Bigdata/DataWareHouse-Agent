"""YAML 规则配置的加载与导出（原文第三章「YAML 配置化」）。

配置按原文的三级结构组织——**检查类型 → 表 → 字段级规则**::

    not_null:
      ods_data_file_meta:
        - rule_id: QG-OSS-009-metadata-required
          field: object_key
          severity: ERROR
          dimension: completeness
          issue_level: P1
          ...
    regex:
      "*":
        - rule_id: QG-COM-002-data-id-format
          field: data_id
          params:
            pattern: "COLLECT_[A-Z0-9]+_\\d{14}_[0-9a-f]{4,}"

PyYAML 是**可选依赖**：本模块用延迟 import，没装 PyYAML 时
:func:`load_rules` 仍可读 JSON 配置，:func:`dump_json` 仍可导出——
保证「客户端库没装也能 import 本包」。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .builtin import BUILTIN_RULES, default_rule_center
from .ruleset import RuleCenter

__all__ = [
    "DEFAULT_RULES_PATH",
    "load_rules",
    "load_into",
    "dump_yaml",
    "dump_json",
    "write_rules",
    "validate_config",
]

#: 内置 YAML 配置的路径（由 ``python -m adas_lakehouse.quality.loader dump`` 生成）
DEFAULT_RULES_PATH: Path = Path(__file__).parent / "rules" / "quality_rules.yaml"


def _require_yaml() -> Any:
    """延迟 import PyYAML，给出可执行的报错信息。"""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            "读写 YAML 规则配置需要 PyYAML（pip install pyyaml）；"
            "若不想引入该依赖，可改用同结构的 JSON 配置文件"
        ) from exc
    return yaml


def _read_tree(path: str | Path) -> dict[str, Any]:
    """读配置文件，按扩展名选解析器。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".json",):
        data = json.loads(text)
    else:
        data = _require_yaml().safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ValueError(
            f"规则配置根节点必须是映射（检查类型 → 表 → 规则列表），实际是 {type(data).__name__}"
        )
    return dict(data)


def load_rules(path: str | Path, *, with_builtin: bool = False) -> RuleCenter:
    """从 YAML/JSON 加载规则中心。

    :param with_builtin: True 时先装内置规则，再用配置里的同 ID 规则覆盖——
        这是生产推荐姿势：内置规则保底，项目配置只写差异。
    """
    tree = _read_tree(path)
    center = default_rule_center() if with_builtin else RuleCenter()
    load_into(center, tree)
    return center


def load_into(center: RuleCenter, tree: Mapping[str, Any]) -> RuleCenter:
    """把三级结构合并进已有规则中心（同 rule_id 覆盖）。"""
    from .rules import RuleSpec

    for check, tables in tree.items():
        if not isinstance(tables, Mapping):
            raise ValueError(f"检查类型 {check!r} 下必须是「表 → 规则列表」的映射")
        for table, rule_list in tables.items():
            if rule_list is None:
                continue
            if not isinstance(rule_list, (list, tuple)):
                raise ValueError(f"{check}.{table} 下必须是规则列表")
            for raw in rule_list:
                if not isinstance(raw, Mapping):
                    raise ValueError(
                        f"{check}.{table} 的规则项必须是映射，实际是 {type(raw).__name__}"
                    )
                center.upsert(RuleSpec.from_dict(raw, table=str(table), check=str(check)))
    return center


def dump_yaml(center: RuleCenter | None = None, *, header: str = "") -> str:
    """把规则中心导出成 YAML 文本（三级结构）。"""
    yaml = _require_yaml()
    rules = center or default_rule_center()
    body = yaml.safe_dump(
        rules.to_yaml_tree(),
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
        width=100,
    )
    return f"{header}\n{body}" if header else body


def dump_json(center: RuleCenter | None = None) -> str:
    """无 PyYAML 环境下的等价导出（同一份三级结构）。"""
    rules = center or default_rule_center()
    return json.dumps(rules.to_yaml_tree(), ensure_ascii=False, indent=2, sort_keys=True)


_HEADER = """\
# 质量门禁规则配置（自动生成，勿手工编辑主干；项目级差异请另建文件覆盖）
#
# 生成命令: python -m adas_lakehouse.quality.loader dump
# 结构: 检查类型 → 表 → 字段级规则（原文三、规则引擎实现）
# 来源: 系列二 · 湖仓实战 第 6 篇《数据质量门禁设计：智驾数据入湖的五步校验链路》
#
# 约定:
#   · severity=ERROR → REJECT 拒绝入湖；WARNING → ALLOW_WITH_FLAG 带标记放行
#   · issue_level=P0 为合规/安全级，必须 ERROR，且不允许灰度/下线
#   · 表名 "*" 表示三通道通用规则
"""


def write_rules(path: str | Path | None = None, center: RuleCenter | None = None) -> Path:
    """把规则导出到文件（默认 :data:`DEFAULT_RULES_PATH`）。"""
    target = Path(path) if path else DEFAULT_RULES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".json":
        target.write_text(dump_json(center), encoding="utf-8")
    else:
        target.write_text(dump_yaml(center, header=_HEADER), encoding="utf-8")
    return target


def validate_config(path: str | Path) -> list[str]:
    """校验一份规则配置，返回问题清单（空列表表示没问题）。

    校验项：
      · 能否解析成 RuleSpec（类型、必填参数、P0 必须 ERROR 等，由 RuleSpec 自身把关）
      · rule_id 是否与内置规则冲突但语义不同
      · 引用的检查类型是否已注册
    """
    problems: list[str] = []
    try:
        center = load_rules(path)
    except Exception as exc:  # noqa: BLE001 - 配置校验要把任何异常翻译成人话
        return [f"配置无法加载: {type(exc).__name__}: {exc}"]

    builtin_ids = {r.rule_id: r for r in BUILTIN_RULES}
    for rule in center:
        conflict = builtin_ids.get(rule.rule_id)
        if conflict is not None and conflict.check is not rule.check:
            problems.append(
                f"规则 {rule.rule_id} 与内置规则同 ID 但检查类型不同"
                f"（内置 {conflict.check.value} vs 配置 {rule.check.value}）"
            )
        if rule.table == "*" and rule.field is None and rule.check.value != "required_flags":
            problems.append(f"规则 {rule.rule_id} 作用于所有表却没有指定 field，范围过宽")
    return problems


def main(argv: list[str] | None = None) -> int:
    """命令行入口::

    python -m adas_lakehouse.quality.loader dump [-o 路径]
    python -m adas_lakehouse.quality.loader validate 配置路径
    python -m adas_lakehouse.quality.loader list
    """
    import argparse

    parser = argparse.ArgumentParser(description="质量门禁规则配置工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump", help="导出内置规则为 YAML/JSON")
    p_dump.add_argument("-o", "--output", default=None, help="输出路径，缺省写回内置配置文件")

    p_val = sub.add_parser("validate", help="校验一份规则配置")
    p_val.add_argument("path", help="配置文件路径")

    sub.add_parser("list", help="列出内置规则清单")

    args = parser.parse_args(argv)
    if args.cmd == "dump":
        target = write_rules(args.output)
        print(f"已导出 {len(default_rule_center())} 条规则 -> {target}")
        return 0
    if args.cmd == "validate":
        problems = validate_config(args.path)
        if not problems:
            print("配置校验通过")
            return 0
        for p in problems:
            print(f"✗ {p}")
        return 1
    center = default_rule_center()
    for row in center.describe():
        print(
            f"{row['rule_id']:<42} {row['table']:<28} {row['check']:<18} "
            f"{row['severity']:<8} {row['issue_level']} {row['disposition']}"
        )
    print(f"合计 {len(center)} 条规则")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
