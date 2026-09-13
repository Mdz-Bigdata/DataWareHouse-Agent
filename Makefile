# =============================================================================
# DataWareHouse-Agent —— 仓库根工程入口
#
# 本仓库是个多项目 monorepo，每个子项目的测试入口各不相同（backend 要
# PYTHONPATH=.，adas 有自己的 Makefile，platform 的用例在仓库根的 tests/）。
# 这里把它们收敛成一组统一命令，并且**与 .github/workflows/ci.yml 跑的是同一套**——
# 本地 `make ci` 通过 ≈ CI 通过，不用 push 上去才发现红。
#
#   make test           三个套件全跑
#   make ci             CI 跑什么，本地就跑什么（密钥扫描 + 卫生 + 三套测试）
#   make scan-secrets   密钥泄露扫描
#
# 注：adas 自带 apps/adas-closed-loop-lakehouse/Makefile（make up/down/ddl/validate…），
#     本文件只转发它的 check，不重复实现。
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
ADAS_DIR := $(ROOT)/apps/adas-closed-loop-lakehouse

# 优先用 backend 的 venv——本地测试基线就跑在它上面；没有则回落到 python3。
PYTHON ?= $(shell test -x "$(ROOT)/backend/venv/bin/python" \
            && echo "$(ROOT)/backend/venv/bin/python" || echo python3)

PYTEST_ARGS ?= -q

# 与 .github/workflows/ci.yml 的 PYTEST_MARKER_FILTER 保持一致：
# CI runner 没有数据库、没有 Docker、没有外网凭据。marker 定义见根 pytest.ini。
CI_MARKERS ?= not requires_db and not requires_docker and not requires_network and not requires_llm_key

# adas 的端口体检在无 Docker 环境下的参数，同 CI。
ADAS_PORTS_ARGS ?= --no-docker --skip-listen

.PHONY: help test test-backend test-platform test-adas \
        scan-secrets scan-secrets-audit hygiene ci clean

help:
	@echo "DataWareHouse-Agent —— 可用目标"
	@echo ""
	@echo "  测试"
	@echo "    test            backend + platform + adas 全套"
	@echo "    test-backend    智能问数后端（backend/tests）"
	@echo "    test-platform   平台网关与统一启动器（tests/platform）"
	@echo "    test-adas       智驾数据闭环湖仓 make check（validate+ports+lint+fmt+test）"
	@echo ""
	@echo "  门禁"
	@echo "    scan-secrets       密钥泄露扫描（豁免清单生效，等同 CI）"
	@echo "    scan-secrets-audit 同上但忽略豁免清单，列出全部历史债"
	@echo "    hygiene            禁止源码里出现硬编码开发机绝对路径"
	@echo "    ci                 CI 跑的全部内容，本地一把过"
	@echo ""
	@echo "  变量：PYTHON= PYTEST_ARGS= CI_MARKERS= ADAS_PORTS_ARGS="

# --- 测试 ---------------------------------------------------------------------
# 与文档基线命令逐字一致：cd backend && PYTHONPATH=. python -m pytest tests/ -q
test-backend:
	cd "$(ROOT)/backend" && PYTHONPATH=. $(PYTHON) -m pytest tests/ $(PYTEST_ARGS)

# pythonpath 由根 pytest.ini 提供，不需要再 export PYTHONPATH。
test-platform:
	cd "$(ROOT)" && $(PYTHON) -m pytest tests/platform $(PYTEST_ARGS)

test-adas:
	$(MAKE) -C "$(ADAS_DIR)" check PORTS_ARGS="$(ADAS_PORTS_ARGS)"

test: test-backend test-platform test-adas

# --- 门禁 ---------------------------------------------------------------------
# 不带 --forbid-present：那个开关只在 CI 有意义（检出目录里只有被跟踪的文件），
# 本地开发机上 llm_config.json / user_memory.json 本来就该存在且未被跟踪。
scan-secrets:
	$(PYTHON) "$(ROOT)/.github/scripts/scan_secrets.py" --root "$(ROOT)"

scan-secrets-audit:
	$(PYTHON) "$(ROOT)/.github/scripts/scan_secrets.py" --root "$(ROOT)" --no-allowlist

# 与 ci.yml 的 hygiene job 同一条正则。grep 无匹配时返回 1，所以用 if 包住。
hygiene:
	@cd "$(ROOT)" && if grep -rnE '(/Users/|/home/)[A-Za-z0-9._-]+/' \
	     backend/app platform_gateway integrations tools tests \
	     --include='*.py' --include='*.ts' --include='*.tsx' \
	     --exclude-dir=__pycache__; then \
	  echo "源码里出现硬编码的开发机绝对路径。改用 backend/app/core/paths.py 的" \
	       "repo_root()/backend_dir()/llm_config_path()，或环境变量" \
	       "DWH_REPO_ROOT / DWH_BACKEND_DIR / DWH_LLM_CONFIG_PATH。"; \
	  exit 1; \
	fi; \
	echo "未发现硬编码绝对路径。"

# CI 等价物。注意 CI 里额外有一步 `git ls-files` 检查 llm_config.json /
# user_memory.json 是否仍被跟踪——那步依赖 git 索引，只在 CI 执行。
ci: scan-secrets hygiene
	$(MAKE) test-backend PYTEST_ARGS='$(PYTEST_ARGS) -m "$(CI_MARKERS)"'
	$(MAKE) test-platform PYTEST_ARGS='$(PYTEST_ARGS) -m "$(CI_MARKERS)"'
	$(MAKE) test-adas

clean:
	find "$(ROOT)/backend" "$(ROOT)/tests" "$(ROOT)/platform_gateway" \
	     -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf "$(ROOT)/.pytest_cache"
