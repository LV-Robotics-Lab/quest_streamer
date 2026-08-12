#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$repo_root/config/quest.env" ]; then
  # shellcheck disable=SC1091
  . "$repo_root/config/quest.env"
fi

cd "$repo_root"
if command -v uv >/dev/null 2>&1; then
  uv sync --extra dev
  python_cmd=(uv run python)
else
  venv="${QUEST_VENV:-$repo_root/.venv}"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --upgrade pip
  "$venv/bin/python" -m pip install -e '.[dev]'
  python_cmd=("$venv/bin/python")
fi

if [ "${RUN_OCULUS_BOOTSTRAP:-0}" = "1" ]; then
  SKIP_APK="${SKIP_APK:-1}" bash scripts/bootstrap_oculus_reader.sh
else
  echo "Skipped diagnostic legacy oculus_reader bootstrap."
fi

echo "[quest-env] python=${python_cmd[*]}"
echo "[quest-env] offline check: ${python_cmd[*]} -m pytest"
echo "[quest-env] controller mode: bash scripts/switch_mode.sh controller"
