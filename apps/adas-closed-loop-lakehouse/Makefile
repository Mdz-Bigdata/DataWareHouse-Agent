# =============================================================================
# 智驾数据闭环湖仓 —— 工程入口
#
#   make up | down | logs      本地依赖栈（Flink / StarRocks / Neo4j / Kafka / MinIO）
#   make ddl                   渲染四层建表脚本到 ddl/
#   make validate              全表硬校验 + 命名偏离审计（CI 可当断言跑）
#   make ports                 端口冲突体检：186xx 号段 vs 同仓平台 vs 宿主机占用
#   make test | lint | fmt     pytest / ruff check / ruff format
#
# 设计约束：ddl / validate / ports / test / lint / fmt 全部**不连任何外部服务**，
# 裸环境（无 Docker、无驱动）也能跑通（ports 会退化解析并明说哪一项没查成）；
# 只有 up/down/logs 需要 Docker。
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- 可覆盖变量 ---------------------------------------------------------------
PYTHON      ?= python3
PIP         ?= $(PYTHON) -m pip
ROOT        := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
SRC         := $(ROOT)/src
DDL_DIR     ?= $(ROOT)/ddl
PYTEST_ARGS ?= -q
# check_ports.py 的透传参数（--no-docker / --skip-listen / --strict / --quiet）
PORTS_ARGS  ?=
RUFF_TARGETS := src tests scripts

# src layout：未 pip install 时也能直接跑，靠 PYTHONPATH 指到 src/
export PYTHONPATH := $(SRC)$(if $(PYTHONPATH),:$(PYTHONPATH),)

# compose 文件位置：优先 docker/compose.yaml，回落到根目录 compose.yaml
COMPOSE_FILE ?= $(firstword $(wildcard $(ROOT)/docker/compose.yaml $(ROOT)/docker/docker-compose.yaml $(ROOT)/compose.yaml))
# docker compose v2 优先，回落到 docker-compose v1
DOCKER_COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
SERVICES    ?=

.PHONY: help up down logs ddl validate consistency stats ports test lint fmt fmt-check install install-dev clean check

# --- 帮助 ---------------------------------------------------------------------
help:
	@echo "智驾数据闭环湖仓 —— 可用目标"
	@echo ""
	@echo "  环境"
	@echo "    install        安装本包（可编辑模式，零第三方运行时依赖）"
	@echo "    install-dev    安装本包 + dev 分组（pytest / ruff / mypy）"
	@echo ""
	@echo "  依赖栈（需要 Docker）"
	@echo "    up             拉起 Flink / StarRocks / Neo4j / Kafka / MinIO"
	@echo "    down           停止并清理容器（保留数据卷）"
	@echo "    logs           跟随查看日志，可用 SERVICES=flink-jobmanager 过滤"
	@echo ""
	@echo "  湖仓契约（离线，不连外部服务）"
	@echo "    ddl            渲染 00_catalog / 10_ods / 20_dwd / 30_dws / 40_ads 到 ddl/"
	@echo "    validate       88 张表硬校验 + 命名偏离审计，有违规则非零退出"
	@echo "    consistency    跨模块一致性体检：子系统/SQL/env/依赖 是否都跟 registry 对得上"
	@echo "    stats          数据域 × 层级 表数统计并与原文口径对账"
	@echo "    ports          端口冲突体检：186xx 发布端口 vs 同仓平台 vs 宿主机占用"
	@echo ""
	@echo "  代码质量"
	@echo "    test           pytest tests/（PYTEST_ARGS=... 可透传参数）"
	@echo "    lint           ruff check"
	@echo "    fmt            ruff format（就地改写）"
	@echo "    fmt-check      ruff format --check（CI 用）"
	@echo "    check          validate + ports + lint + fmt-check + test"
	@echo "    clean          清理 __pycache__ / .pytest_cache / 构建产物"

# --- 环境 ---------------------------------------------------------------------
install:
	$(PIP) install -e $(ROOT)

install-dev:
	$(PIP) install -e "$(ROOT)[dev]"

# --- 依赖栈 -------------------------------------------------------------------
# COMPOSE_FILE 为空说明 docker/compose.yaml 还没生成，给出可执行的提示而不是晦涩报错。
define require_compose
	@test -n "$(COMPOSE_FILE)" || { \
		echo "找不到 compose 文件：请提供 docker/compose.yaml，或 make up COMPOSE_FILE=/path/to/compose.yaml"; \
		exit 1; }
endef

up:
	$(require_compose)
	$(DOCKER_COMPOSE) -f "$(COMPOSE_FILE)" up -d $(SERVICES)
	@echo "依赖栈已启动。查看状态：$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) ps"

down:
	$(require_compose)
	$(DOCKER_COMPOSE) -f "$(COMPOSE_FILE)" down --remove-orphans

logs:
	$(require_compose)
	$(DOCKER_COMPOSE) -f "$(COMPOSE_FILE)" logs -f --tail=200 $(SERVICES)

# --- 湖仓契约 -----------------------------------------------------------------
ddl:
	$(PYTHON) -m adas_lakehouse.cli ddl-export --out "$(DDL_DIR)"

validate:
	$(PYTHON) -m adas_lakehouse.cli catalog-validate

# 跨模块一致性：表名 / 列契约 / 本地 TableSpec / SQL 列引用 / 环境变量 / 依赖
# ⚠️ 目前**尚未全绿**（子系统与 catalog 的列定义漂移待收敛），所以不进 check，
#    但每一条输出都是真实缺口，别把它当噪音。
consistency:
	$(PYTHON) $(ROOT)/scripts/check_consistency.py

stats:
	$(PYTHON) -m adas_lakehouse.cli catalog-stats

# 端口冲突体检：本项目 186xx 独占号段 vs 同仓平台 compose vs 宿主机当前 LISTEN。
# 不强依赖 Docker：装了就用 `docker compose config` 的权威口径，没装/报错则退化为
# 纯标准库解析 YAML；lsof 查不到时会明确报「未检查」而不是静默放行。
# 有冲突非零退出，所以它能进 check。
ports:
	$(PYTHON) $(ROOT)/scripts/check_ports.py $(PORTS_ARGS)

# --- 代码质量 -----------------------------------------------------------------
test:
	$(PYTHON) -m pytest tests/ $(PYTEST_ARGS)

lint:
	$(PYTHON) -m ruff check $(RUFF_TARGETS)

fmt:
	$(PYTHON) -m ruff format $(RUFF_TARGETS)

fmt-check:
	$(PYTHON) -m ruff format --check $(RUFF_TARGETS)

check: validate ports lint fmt-check test

clean:
	find $(ROOT)/src $(ROOT)/tests $(ROOT)/scripts -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(ROOT)/.pytest_cache $(ROOT)/.ruff_cache $(ROOT)/.mypy_cache \
	       $(ROOT)/build $(ROOT)/dist $(ROOT)/src/*.egg-info
