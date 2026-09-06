#!/usr/bin/env bash
# Start the complete local workspace from any working directory.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$repo_dir/backend/venv/bin/python" ]]; then
  launcher_python="$repo_dir/backend/venv/bin/python"
else
  launcher_python="${PLATFORM_PYTHON:-python3}"
fi
exec "$launcher_python" "$repo_dir/tools/start_local.py" "$@"
