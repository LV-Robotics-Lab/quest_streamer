#!/usr/bin/env bash
set -euo pipefail

duration=15
mode=polling

while [ "$#" -gt 0 ]; do
  case "$1" in
    --duration)
      duration="$2"
      shift 2
      ;;
    --mode)
      mode="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: bash scripts/run_controller_smoke.sh [--duration 15] [--mode polling|callback]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$repo_root/scripts/run_example.sh" \
  teleop_wrapper.py "$mode" --duration "$duration"
