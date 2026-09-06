# Repository Guidelines

## Project Structure & Module Organization

- `app/agent/`: LangGraph state, graph construction, and query nodes.
- `app/api/`: FastAPI routers and request schemas; the main query endpoint is `/api/query`.
- `app/clients/` and `app/repositories/`: integrations and persistence adapters for MySQL, Elasticsearch, and Qdrant.
- `app/services/`, `app/entities/`, `app/models/`, and `app/mappers/`: application services and data models.
- `conf/`: YAML runtime and metadata configuration. `prompts/`: LLM prompt templates.
- `frontend/`: React + TypeScript + Vite analytics workbench (Carbon Design System); see `frontend/README.md`.
- `specs/`: product requirements and domain notes, including `specs/PRD.md`.

## Build, Test, and Development Commands

Use Python 3.12+ and `uv`:

```bash
uv sync                                     # Install locked dependencies
uv run uvicorn main:app --reload            # Run the FastAPI service locally
python -m compileall -q app tests           # Check Python syntax
uv lock --check                             # Verify uv.lock is current
uv run python -m app.scripts.build_meta_knowledge --domain audio
```

The metadata build command requires reachable MySQL, Elasticsearch, Qdrant, and embedding services. Configure local connection values in `conf/app_config.local.yaml` or via environment variables before running it.

Frontend workbench (Node 20+):

```bash
cd frontend
npm ci                  # Install locked dependencies
npm run dev             # Vite dev server on :5173, proxies /api to :8000
npm run typecheck       # tsc --noEmit
npm run test            # Vitest unit + React Testing Library tests
npm run build           # Production build into frontend/dist
npm run e2e             # Playwright (run `npx playwright install chromium` once first)
```

The frontend deploys as a static SPA behind Nginx (`frontend/Dockerfile`, `frontend/nginx.conf`); `infra/compose.app.yaml` exposes only the `web` service and keeps FastAPI on the internal Docker network.

## Development and Release Workflow

- 默认先在本地开发环境验证改动：后端使用 `uv run uvicorn main:app --reload`，前端使用 `npm run dev`。
- 未经用户明确要求“上线生产”，不要构建或重启前后端 Docker 镜像/容器。
- 用户完成本地验收并明确要求“上线生产”后，才构建对应 Docker 服务并进行生产健康检查。

## Coding Style & Naming Conventions

Use four-space indentation, type hints, and PEP 8-compatible Python. Prefer `async`/`await` for database and network operations. Use `snake_case` for modules, functions, variables, and YAML keys; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep the existing layered design: routers call services, services use repositories, and graph nodes coordinate agent state.

## Testing Guidelines

Tests live under `tests/` and are run with pytest:

```bash
uv run pytest -q
$env:RUN_AUDIO_DATA_ACCEPTANCE='1'; uv run pytest -q -m integration   # PowerShell
```

For every change, run `python -m compileall -q app tests` and exercise the affected endpoint or service locally. When adding tests, place them under `tests/`, name files `test_*.py`, and use pytest conventions; add test dependencies to `pyproject.toml` only when needed.

## Commit & Pull Request Guidelines

Use concise Conventional Commit-style subjects such as `feat: add audio metadata`, `fix: handle empty SQL results`, `docs: update setup`, or `chore: refresh lockfile`. Keep commits focused. Pull requests should describe the behavior change, list validation commands, identify configuration or schema changes, and include API screenshots or example requests when they clarify the change. Never commit real API keys, passwords, or private connection details.

## Security & Configuration

Keep secrets out of tracked files. `conf/app_config.yaml` should contain placeholders only; use local, ignored configuration for credentials. Rebuild metadata indexes after changing table or metric definitions, and verify generated SQL against the intended database before sharing results.

All `/api/*` endpoints except `/api/auth/login` require a Bearer JWT (`app/api/deps.py:get_current_user`); query traces are owner-scoped via `query_trace.user_id`. Passwords use PBKDF2 (`app/core/security.py`); `AUTH_SECRET_KEY` signs tokens and must be set per environment.
