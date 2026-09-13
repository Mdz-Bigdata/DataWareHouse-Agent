#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

command_name="${1:-help}"
[ "$#" -gt 0 ] && shift
python_command="${PLATFORM_PYTHON:-python3}"
env_file=".env.platform"
compose=(docker compose --env-file "$env_file")
preflight_script="tools/port_preflight.py"

# ---------------------------------------------------------------------------
# Build mode
#
#   auto  (default) -- plain `compose up -d`. Compose's own build policy applies:
#                      a service whose image is MISSING is built, a service whose
#                      image already exists is reused as-is. This is what makes
#                      `up-*` idempotent: re-running with no source change neither
#                      rebuilds nor recreates anything.
#   force (--build) -- `compose up -d --build`. Rebuilds every build: service
#                      unconditionally. This was the OLD hardcoded default.
#   never (--no-build) -- `compose up -d --no-build`. Never builds, not even a
#                      missing image (compose fails instead). For CI/offline runs
#                      that must use pre-built images only.
# ---------------------------------------------------------------------------
build_mode="auto"
build_flag_seen=""
positional=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --build)
      if [ "$build_flag_seen" = "--no-build" ]; then
        echo "[platform.sh] --build 与 --no-build 互斥，请只给一个。" >&2
        exit 1
      fi
      build_mode="force"
      build_flag_seen="--build"
      ;;
    --no-build)
      if [ "$build_flag_seen" = "--build" ]; then
        echo "[platform.sh] --build 与 --no-build 互斥，请只给一个。" >&2
        exit 1
      fi
      build_mode="never"
      build_flag_seen="--no-build"
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do
        positional+=("$1")
        shift
      done
      break
      ;;
    -*)
      echo "[platform.sh] 未知选项: $1（可用: --build --no-build）" >&2
      exit 1
      ;;
    *)
      positional+=("$1")
      ;;
  esac
  shift
done

target_arg="${positional[0]:-}"

# Commands that never invoke `compose up` must not silently swallow a build flag.
reject_build_flag() {
  if [ -n "$build_flag_seen" ]; then
    echo "[platform.sh] $build_flag_seen 只对 up-* / rebuild 有意义，命令 '$command_name' 不构建任何镜像。" >&2
    exit 1
  fi
}

# The port preflight cannot run without Python, and "no preflight" must never
# degrade into "start anyway". Fail loudly before anything else happens.
require_python() {
  if ! command -v "$python_command" >/dev/null 2>&1; then
    echo "[platform.sh] 找不到 Python 解释器 '$python_command'（可用 PLATFORM_PYTHON 指定）。" >&2
    echo "[platform.sh] 未能检查端口占用，已中止 —— 没有启动任何容器。" >&2
    exit 1
  fi
}

ensure_config() {
  require_python
  "$python_command" integrations/nanzi/configure.py --output "$env_file"
}

# ---------------------------------------------------------------------------
# Target selection
#
# One place decides, per up-* command: which PLATFORM_*_ENABLED flags to export,
# which --profile / service names `compose up` gets, and which selectors the port
# preflight uses. The preflight never hardcodes a port list -- it resolves the
# published host ports from `docker compose config`, so a service added to
# compose.yaml later is covered automatically.
# ---------------------------------------------------------------------------
compose_profile_args=()
compose_service_args=()
preflight_args=()

select_target() {
  local target="$1"
  compose_profile_args=()
  compose_service_args=()
  preflight_args=()

  case "$target" in
    core|data-engine)
      PLATFORM_DATA_API_ENABLED=false
      PLATFORM_AGENTS_ENABLED=false
      PLATFORM_AUDIO_ENABLED=false
      PLATFORM_DATA_ENGINE_ENABLED=true
      compose_service_args=(platform-gateway core-backend core-web data-engine)
      preflight_args=(--service platform-gateway --service core-backend --service core-web --service data-engine)
      ;;
    data-api)
      PLATFORM_DATA_API_ENABLED=true
      PLATFORM_AGENTS_ENABLED=false
      PLATFORM_AUDIO_ENABLED=false
      PLATFORM_DATA_ENGINE_ENABLED=true
      compose_profile_args=(--profile data-api)
      preflight_args=(--profile data-api)
      ;;
    agents)
      PLATFORM_DATA_API_ENABLED=true
      PLATFORM_AGENTS_ENABLED=true
      PLATFORM_AUDIO_ENABLED=false
      PLATFORM_DATA_ENGINE_ENABLED=true
      compose_profile_args=(--profile agents)
      preflight_args=(--profile agents)
      ;;
    nanzi)
      PLATFORM_DATA_API_ENABLED=true
      PLATFORM_AGENTS_ENABLED=true
      PLATFORM_AUDIO_ENABLED=false
      PLATFORM_DATA_ENGINE_ENABLED=true
      compose_profile_args=(--profile nanzi)
      preflight_args=(--profile nanzi)
      ;;
    audio)
      PLATFORM_DATA_API_ENABLED=false
      PLATFORM_AGENTS_ENABLED=false
      PLATFORM_AUDIO_ENABLED=true
      PLATFORM_DATA_ENGINE_ENABLED=true
      compose_profile_args=(--profile audio)
      preflight_args=(--profile audio)
      ;;
    full)
      PLATFORM_DATA_API_ENABLED=true
      PLATFORM_AGENTS_ENABLED=true
      PLATFORM_AUDIO_ENABLED=true
      PLATFORM_DATA_ENGINE_ENABLED=true
      compose_profile_args=(--profile full)
      preflight_args=(--profile full)
      ;;
    *)
      echo "未知目标: $target（可用: core data-engine data-api agents nanzi audio full）" >&2
      return 1
      ;;
  esac

  export PLATFORM_DATA_API_ENABLED PLATFORM_AGENTS_ENABLED \
    PLATFORM_AUDIO_ENABLED PLATFORM_DATA_ENGINE_ENABLED
}

# run_preflight MODE TARGET -- exits non-zero if any published host port is
# unusable, or if occupancy could not be determined at all. Never silently
# passes: an unavailable probe is treated as a failure.
run_preflight() {
  local mode="$1"
  local target="$2"

  require_python
  if [ ! -f "$preflight_script" ]; then
    echo "[preflight] 未能检查端口占用：缺少 $preflight_script" >&2
    return 1
  fi

  "$python_command" "$preflight_script" \
    --env-file "$env_file" \
    --mode "$mode" \
    --target "$target" \
    ${preflight_args[@]+"${preflight_args[@]}"}
}

up_target() {
  local target="$1"
  local mode="${2:-$build_mode}"
  select_target "$target"
  ensure_config

  # The port preflight stays where it was: BEFORE compose up, gating it. Build
  # mode changes only what happens after the gate opens.
  if ! run_preflight gate "$target"; then
    echo "" >&2
    echo "[platform.sh] 端口预检未通过，已中止 up-$target —— 没有启动任何容器。" >&2
    echo "[platform.sh] 排查用: ./platform.sh ports $target" >&2
    exit 1
  fi

  local build_args=()
  case "$mode" in
    force)
      build_args=(--build)
      echo "[platform.sh] 构建模式: --build（强制重建全部 build: 服务）"
      ;;
    never)
      build_args=(--no-build)
      echo "[platform.sh] 构建模式: --no-build（缺镜像会直接失败，不会构建）"
      ;;
    auto)
      build_args=()
      echo "[platform.sh] 构建模式: 默认（缺镜像才构建；已有镜像直接复用，不重建、不 recreate）"
      echo "[platform.sh] 需要强制重建请用: ./platform.sh rebuild $target  或  ./platform.sh up-$target --build"
      ;;
  esac

  "${compose[@]}" \
    ${compose_profile_args[@]+"${compose_profile_args[@]}"} \
    up -d \
    ${build_args[@]+"${build_args[@]}"} \
    ${compose_service_args[@]+"${compose_service_args[@]}"}
}

case "$command_name" in
  init)
    reject_build_flag
    ensure_config
    ;;
  verify)
    reject_build_flag
    ensure_config
    "$python_command" tools/audit_imports.py
    "$python_command" -m unittest discover -s tests/platform -v
    "${compose[@]}" --profile nanzi config --quiet
    ;;
  ports)
    # Read-only diagnostic: only generate credentials if they are missing at all,
    # so a health check never rewrites a working .env.platform.
    reject_build_flag
    require_python
    [ -f "$env_file" ] || ensure_config
    select_target "${target_arg:-full}"
    run_preflight report "${target_arg:-full}"
    ;;
  up-core)
    up_target core
    ;;
  up-data-engine)
    up_target data-engine
    ;;
  up-data-api)
    up_target data-api
    ;;
  up-agents)
    up_target agents
    ;;
  up-nanzi)
    up_target nanzi
    ;;
  up-audio)
    up_target audio
    ;;
  up-full)
    up_target full
    ;;
  rebuild)
    # Explicit rebuild entry point: same as `up-TARGET --build`, and the one
    # place where "rebuild everything" is the documented intent rather than an
    # accident of the default.
    if [ "$build_mode" = "never" ]; then
      echo "[platform.sh] rebuild 与 --no-build 冲突。" >&2
      exit 1
    fi
    up_target "${target_arg:-full}" force
    ;;
  status)
    reject_build_flag
    "${compose[@]}" --profile full ps
    ;;
  down)
    reject_build_flag
    "${compose[@]}" --profile full down
    ;;
  *)
    cat <<'USAGE'
Usage: ./platform.sh COMMAND [TARGET] [--build | --no-build]

  init        Generate private .env.platform credentials once (no containers started)
  verify      Verify imported snapshots, platform tests, and Compose config
  ports [T]   Host-port health table for target T (default: full). No containers touched.
  up-core     Start the existing DataWareHouse-Agent and unified gateway
  up-data-engine Start core plus the deterministic DSH/OAG Data Agent engine
  up-data-api Start core plus NanZi Data API capabilities
  up-agents   Start core plus NanZi AI Agent and its Data API dependency
  up-nanzi    Build and initialize both complete NanZi web applications plus core
  up-audio    Start core plus Listen Book Agent capabilities
  up-full     Start every application and infrastructure dependency
  rebuild [T] Force-rebuild every image for target T (default: full), then start it
  status      Show all service states
  down        Stop services without deleting persistent volumes

Targets for `ports` and `rebuild`: core  data-engine  data-api  agents  nanzi  audio  full

Build behaviour (CHANGED -- the old default rebuilt everything, every time)
  OLD: every up-* ran `compose up -d --build`, so running up-full twice with no
       source change still rebuilt and recreated all 10 build: services.
  NEW: up-* runs plain `compose up -d`. Compose's default build policy applies:

    image missing (first run, new service, after `down --rmi`) -> BUILT automatically
    image already present                                      -> reused as-is;
                                                                  no rebuild, and
                                                                  no recreate of a
                                                                  container whose
                                                                  config is unchanged

  So up-full is now idempotent, and a first run on a clean machine still works
  without any flag. Source changes are NOT auto-detected -- Compose compares
  image existence, not file mtimes -- so after editing code you must ask for a
  rebuild explicitly:

    ./platform.sh rebuild            # force-rebuild everything in `full`
    ./platform.sh rebuild agents     # force-rebuild just the `agents` target
    ./platform.sh up-full --build    # identical to `rebuild`, old default behaviour

  --build     Force `compose up -d --build`: rebuild all build: services.
  --no-build  Force `compose up -d --no-build`: never build, not even a missing
              image (compose fails instead). For offline/CI runs that must only
              consume pre-built images.
  Both flags are up-*/rebuild only; other commands reject them instead of
  ignoring them. They are mutually exclusive.

Port preflight
  Every up-* command resolves the host ports it is about to publish from
  `docker compose config` (never a hardcoded list) and checks each one first:

    free                      -> proceed
    held by this project      -> proceed (normal restart)
    held by anything else     -> ABORT before starting a single container,
                                 printing the port, the owning process/container,
                                 and how to resolve it

  A conflicting port can be moved instead of freed, e.g.

    export PLATFORM_CORE_BACKEND_PORT=8010 && ./platform.sh up-full

  Overridable vars: PLATFORM_GATEWAY_PORT, PLATFORM_CORE_BACKEND_PORT,
  PLATFORM_CORE_WEB_PORT, PLATFORM_DATA_API_PORT, PLATFORM_AGENTS_PORT,
  PLATFORM_AUDIO_WEB_PORT, PLATFORM_REDIS_PORT, PLATFORM_QDRANT_PORT,
  PLATFORM_ELASTICSEARCH_PORT.

  If occupancy cannot be determined (no socket/lsof/nc probe available), the
  preflight aborts and says so rather than passing silently.
USAGE
    ;;
esac
