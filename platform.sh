#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

command_name="${1:-help}"
python_command="${PLATFORM_PYTHON:-python3}"
compose=(docker compose --env-file .env.platform)

ensure_config() {
  "$python_command" integrations/nanzi/configure.py --output .env.platform
}

case "$command_name" in
  init)
    ensure_config
    ;;
  verify)
    ensure_config
    "$python_command" tools/audit_imports.py
    "$python_command" -m unittest discover -s tests/platform -v
    "${compose[@]}" --profile nanzi config --quiet
    ;;
  up-core)
    ensure_config
    PLATFORM_DATA_API_ENABLED=false PLATFORM_AGENTS_ENABLED=false PLATFORM_AUDIO_ENABLED=false \
      "${compose[@]}" up -d --build platform-gateway core-backend core-web
    ;;
  up-data-api)
    ensure_config
    PLATFORM_DATA_API_ENABLED=true PLATFORM_AGENTS_ENABLED=false PLATFORM_AUDIO_ENABLED=false \
      "${compose[@]}" --profile data-api up -d --build
    ;;
  up-agents)
    ensure_config
    PLATFORM_DATA_API_ENABLED=true PLATFORM_AGENTS_ENABLED=true PLATFORM_AUDIO_ENABLED=false \
      "${compose[@]}" --profile agents up -d --build
    ;;
  up-nanzi)
    ensure_config
    PLATFORM_DATA_API_ENABLED=true PLATFORM_AGENTS_ENABLED=true PLATFORM_AUDIO_ENABLED=false \
      "${compose[@]}" --profile nanzi up -d --build
    ;;
  up-audio)
    ensure_config
    PLATFORM_DATA_API_ENABLED=false PLATFORM_AGENTS_ENABLED=false PLATFORM_AUDIO_ENABLED=true \
      "${compose[@]}" --profile audio up -d --build
    ;;
  up-full)
    ensure_config
    PLATFORM_DATA_API_ENABLED=true PLATFORM_AGENTS_ENABLED=true PLATFORM_AUDIO_ENABLED=true \
      "${compose[@]}" --profile full up -d --build
    ;;
  status)
    "${compose[@]}" --profile full ps
    ;;
  down)
    "${compose[@]}" --profile full down
    ;;
  *)
    cat <<'USAGE'
Usage: ./platform.sh COMMAND

  init        Generate private .env.platform credentials once (no containers started)
  verify      Verify imported snapshots, platform tests, and Compose config
  up-core     Start the existing DataWareHouse-Agent and unified gateway
  up-data-api Start core plus NanZi Data API capabilities
  up-agents   Start core plus NanZi AI Agent and its Data API dependency
  up-nanzi    Build and initialize both complete NanZi web applications plus core
  up-audio    Start core plus Listen Book Agent capabilities
  up-full     Start every application and infrastructure dependency
  status      Show all service states
  down        Stop services without deleting persistent volumes
USAGE
    ;;
esac
