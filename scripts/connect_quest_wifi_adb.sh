#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/connect_quest_wifi_adb.sh <quest-ip> [port]

This is a fallback when the Linux host cannot enumerate Quest over USB, but
ADB-over-Wi-Fi has already been enabled from another machine.

Example:
  bash scripts/connect_quest_wifi_adb.sh 192.168.1.42
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] || [ "$#" -lt 1 ]; then
  usage
  exit 0
fi

ip="$1"
port="${2:-5555}"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not found" >&2
  exit 1
fi

adb start-server >/dev/null
adb connect "$ip:$port"
adb devices -l
