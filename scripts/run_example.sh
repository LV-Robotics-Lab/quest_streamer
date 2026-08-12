#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/run_example.sh <example.py> [args...]

Examples:
  bash scripts/run_example.sh print_raw_data.py
  bash scripts/run_example.sh teleop_wrapper.py polling --duration 15
  bash scripts/run_example.sh print_all_buttons.py
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] || [ "$#" -lt 1 ]; then
  usage
  exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$repo_root/config/quest.env" ]; then
  # shellcheck disable=SC1090
  . "$repo_root/config/quest.env"
fi

src="${QUEST_STREAMER_SOURCE:-$repo_root}"
case "$src" in
  /*) ;;
  *) src="$repo_root/$src" ;;
esac

example="$1"
shift
case "$example" in
  */*|*.py) ;;
  *) example="$example.py" ;;
esac

example_path="$src/examples/$example"
if [ ! -f "$example_path" ]; then
  echo "QuestStreamer example not found: $example_path" >&2
  exit 1
fi

cd "$src"

if command -v uv >/dev/null 2>&1; then
  exec uv run python "examples/$example" "$@"
fi

venv="${QUEST_VENV:-$repo_root/.venv}"
if [ -x "$venv/bin/python" ]; then
  exec "$venv/bin/python" "examples/$example" "$@"
fi

if [ -x .venv/bin/python ]; then
  exec .venv/bin/python "examples/$example" "$@"
fi

exec python3 "examples/$example" "$@"
