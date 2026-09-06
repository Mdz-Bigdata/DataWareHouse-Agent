# Audiobook Demo Data

This directory contains the database assets required by the query system. It is
independent from the source project's business FastAPI application.

- `sql/audio.sql`: MySQL 8 DDL for all 54 audiobook tables.
- `seeds/`: curated CSV seed data.
- `generate/`: deterministic six-layer data generator and acceptance checks.
- `bootstrap.py`: safe schema creation and data-generation entry point.

Set `AUDIO_DB_HOST`, `AUDIO_DB_PORT`, `AUDIO_DB_USER`,
`AUDIO_DB_PASSWORD`, and optionally `AUDIO_DB_NAME` (default `audio`). Then run:

```bash
uv sync --group data
uv run python -m tools.audio_data.bootstrap --profile smoke
uv run python -m tools.audio_data.bootstrap --profile full --reset
```

If the schema already exists, bootstrap stops without changing it. Destructive
rebuilds require the explicit `--reset` option. Use `--schema-only` when only the
DDL should be applied.
