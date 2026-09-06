# Bundled applications

The original DataWareHouse-Agent remains in `backend/` and `frontend/`. Three
authorized upstream snapshots are imported below as independent applications:

| Application | Path | Default internal port | Gateway prefix |
|---|---|---:|---|
| DataWareHouse-Agent | `backend/`, `frontend/` | 8000 / 3000 | `/platform/core` |
| NanZi API Data Platform | `apps/nanzi-api-data-platform` | 8020 | `/platform/data-api` |
| NanZi AI Agent Platform | `apps/nanzi-ai-agent-platform` | 8030 | `/platform/agents` |
| Listen Book Data Agent | `apps/listen-book-data-agent` | 8040 | `/platform/audio` |

Each imported application intentionally retains its own Python and frontend
dependency locks. Do not flatten these dependencies into the root application:
the source projects use conflicting Python, FastAPI, React, Vue, Redis, and
SQLAlchemy versions. Integration is provided by `platform_gateway/`, isolated
application sessions, and the unified portal.

Run `./platform.sh init`, then `./platform.sh up-nanzi` to build and initialize
both complete NanZi applications. The portal opens their native full web UIs
on ports 8020 and 8030. See [the integration guide](../integrations/nanzi/README.md)
for private login credentials, first-run database behavior and business setup.

Source commits and licenses are recorded in `THIRD_PARTY.yml` and `NOTICE.md`.
