"""命令行装配层：四条命令必须能在裸环境里当 CI 断言跑。

零副作用读取：除 ddl-export 外的子命令只读 catalog.registry，不碰文件系统、
不连任何外部组件。ddl-export 写盘，这里全部导到 tmp_path。
"""

from __future__ import annotations

import pytest

from adas_lakehouse import cli
from adas_lakehouse.domains import Layer

LAYER_COUNTS = {Layer.ODS: 33, Layer.DWD: 30, Layer.DWS: 14, Layer.ADS: 11}


# --------------------------------------------------------------------------- 只读命令


def test_catalog_validate_exits_zero_on_a_clean_catalog(capsys):
    assert cli.main(["catalog-validate"]) == 0
    out = capsys.readouterr().out
    assert "catalog-validate" in out


def test_catalog_validate_list_flag_prints_the_deviation_tables(capsys):
    assert cli.main(["catalog-validate", "--list"]) == 0
    assert capsys.readouterr().out


def test_catalog_stats_reconciles_with_the_source(capsys):
    assert cli.main(["catalog-stats"]) == 0
    out = capsys.readouterr().out
    assert "88" in out
    assert "伪域" in out
    for domain_name in ("采集域", "闭环域", "挖掘域"):
        assert domain_name in out


def test_id_demo_prints_all_three_levels(capsys):
    assert cli.main(["id-demo", "--vehicle", "BP"]) == 0
    out = capsys.readouterr().out
    assert "COLLECT_BP_" in out
    assert "run_" in out


def test_no_subcommand_prints_help_and_returns_two(capsys):
    assert cli.main([]) == 2
    assert "adas-lakehouse" in capsys.readouterr().out


def test_version_flag_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0


def test_unknown_subcommand_is_rejected():
    with pytest.raises(SystemExit):
        cli.main(["no-such-command"])


# --------------------------------------------------------------------------- ddl-export


def test_ddl_export_writes_catalog_plus_four_layer_scripts(tmp_path, capsys):
    assert cli.main(["ddl-export", "--out", str(tmp_path)]) == 0
    capsys.readouterr()

    expected = {cli.CATALOG_FILE, *cli.LAYER_FILES.values()}
    assert {p.name for p in tmp_path.iterdir()} == expected

    for layer, filename in cli.LAYER_FILES.items():
        sql = (tmp_path / filename).read_text(encoding="utf-8")
        assert sql.count("CREATE TABLE IF NOT EXISTS") == LAYER_COUNTS[layer]


def test_layer_file_prefixes_force_execution_order():
    """00 → 10 → 20 → 30 → 40：四层脚本用的是三段式全限定表名，catalog 必须先建。"""
    assert cli.CATALOG_FILE.startswith("00_")
    names = [cli.LAYER_FILES[layer] for layer in Layer]
    assert names == sorted(names)
    assert names == ["10_ods.sql", "20_dwd.sql", "30_dws.sql", "40_ads.sql"]


def test_ddl_export_single_layer_skips_the_others(tmp_path, capsys):
    assert cli.main(["ddl-export", "--layer", "dwd", "--out", str(tmp_path)]) == 0
    capsys.readouterr()
    assert {p.name for p in tmp_path.iterdir()} == {cli.LAYER_FILES[Layer.DWD]}


def test_ddl_export_is_deterministic(tmp_path, capsys):
    """同样的注册表导两次必须逐字节相同，否则 ddl/ 的 diff 全是噪声。"""
    first, second = tmp_path / "a", tmp_path / "b"
    cli.main(["ddl-export", "--out", str(first)])
    cli.main(["ddl-export", "--out", str(second)])
    capsys.readouterr()

    for name in {cli.CATALOG_FILE, *cli.LAYER_FILES.values()}:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_catalog_script_never_writes_a_secret_in_clear_text():
    """口令只写 ${MINIO_SECRET_KEY} 占位符——生成的 SQL 是要进版本库的。"""
    sql = cli.render_catalog_script()
    assert "${MINIO_SECRET_KEY}" in sql
    assert "CREATE CATALOG" in sql.upper()


def test_layer_script_carries_the_regeneration_hint():
    """脚本是生成物，头上必须写明怎么重新生成，别让人手改。"""
    sql = cli.render_layer_script(Layer.ADS)
    assert "scripts/export_ddl.py" in sql or "adas_lakehouse.cli" in sql
