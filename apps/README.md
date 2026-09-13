# Bundled applications

The original DataWareHouse-Agent remains in `backend/` and `frontend/`. Four
authorized upstream snapshots are imported below as independent applications,
plus one application generated in-repo (not an upstream import):

| Application | Path | Default internal port | Gateway prefix |
|---|---|---:|---|
| DataWareHouse-Agent | `backend/`, `frontend/` | 8000 / 3000 | `/platform/core` |
| NanZi API Data Platform | `apps/nanzi-api-data-platform` | 8020 | `/platform/data-api` |
| NanZi AI Agent Platform | `apps/nanzi-ai-agent-platform` | 8030 | `/platform/agents` |
| Listen Book Data Agent | `apps/listen-book-data-agent` | 8040 | `/platform/audio` |
| data-agent-engine | `apps/data-agent-engine` | 8050 | `/platform/data-engine` |
| ADAS Closed-Loop Lakehouse | `apps/adas-closed-loop-lakehouse` | — (no HTTP service) | — (not gateway-mounted) |

Each imported application intentionally retains its own Python and frontend
dependency locks. Do not flatten these dependencies into the root application:
the source projects use conflicting Python, FastAPI, React, Vue, Redis, and
SQLAlchemy versions. Integration is provided by `platform_gateway/`, isolated
application sessions, and the unified portal.

Run `./platform.sh init`, then `./platform.sh up-full` to build the deterministic
engine and initialize both complete NanZi applications. The portal opens their native full web UIs
on ports 8020 and 8030. See [the integration guide](../integrations/nanzi/README.md)
for private login credentials, first-run database behavior and business setup.

## ADAS Closed-Loop Lakehouse (in-repo original work)

`apps/adas-closed-loop-lakehouse` is **not** an upstream import. It was generated
in this repository from a 16-article public technical series on autonomous-driving
data closed-loop lakehouse architecture, and therefore has no upstream repository,
commit, or third-party license to record. For that reason it is intentionally
absent from `THIRD_PARTY.yml`, `NOTICE.md`, and `provenance/*.sha256` — those three
files track vendored third-party snapshots only.

It exposes no HTTP service of its own (`ads/gateway.py` is deliberately
transport-agnostic), so it is not mounted behind `platform_gateway/`. It is
operated through its own `Makefile` and `docker/compose.yaml`.

✅ **Runs alongside the bundled platform.** Its dependency stack owns the host
port range **186xx** exclusively, which is disjoint from every port the platform
publishes (8080 / 8000 / 3000 / 8020 / 8030 / 8040 / 6379 / 6333 / 9200). You can
run `./platform.sh up-full` and this application's `make up` at the same time, in
either order, with **no manual port overrides**.

| Service | Container port | Host port | Purpose |
|---|---:|---:|---|
| MinIO S3 API | 9000 | **18600** | S3 protocol, Paimon warehouse root |
| MinIO Console | 9001 | **18601** | Web console |
| MySQL | 3306 | **18606** | CDC source DB + mining control-plane DB |
| StarRocks query | 9030 | **18630** | MySQL-protocol query port |
| StarRocks FE HTTP | 8030 | **18631** | FE web UI / REST |
| StarRocks BE HTTP | 8040 | **18640** | Stream Load endpoint |
| Neo4j Browser | 7474 | **18674** | Lineage graph web UI |
| Neo4j Bolt | 7687 | **18687** | Bolt protocol |
| Redis | 6379 | **18679** | Control-plane state |
| Flink Web UI | 8081 | **18681** | JobManager REST / web UI |
| Flink SQL Gateway | 8083 | **18683** | SQL Gateway REST |
| Kafka | 9092 | **18692** | Host-side bootstrap |

Host ports are `186` + the last two digits of the container port (`8030` would
collide with `9030`→`18630`, so FE HTTP takes `18631`). **Only the published host
ports were remapped — container ports are unchanged**, and service-to-service
traffic inside the compose network still uses service names with container ports
(`minio:9000`, `kafka:29092`, `mysql:3306`, `bolt://neo4j:7687`).

Range selection rationale and the per-setting migration rules are in
[`apps/adas-closed-loop-lakehouse/docs/ports.md`](adas-closed-loop-lakehouse/docs/ports.md).

Provenance of the source material and every deviation from it are recorded in
`apps/adas-closed-loop-lakehouse/docs/source-deviations.md` and `docs/articles.md`.

Source commits and licenses are recorded in `THIRD_PARTY.yml` and `NOTICE.md`.
The data-agent-engine retains its native MCP/DSH integration and also gains an
HTTP adapter and an in-product workbench for the unified portal.
