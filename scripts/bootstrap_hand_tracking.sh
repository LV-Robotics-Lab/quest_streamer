#!/usr/bin/env bash
# Bootstrap the hand-tracking pipeline:
#   1. pip-install `hand-tracking-sdk` from PyPI.
#   2. adb-install ./assets/hand_tracking_streamer.apk onto the Quest.
#
# Usage:
#     uv sync                                       # create .venv
#     bash scripts/bootstrap_hand_tracking.sh       # install
#
# Overrides:
#     HAND_TRACKING_SDK_VERSION=1.1.0               # pip install pin
#     SKIP_APK=1                                    # SDK only
#
# The SDK requires Python >=3.12, which is why it is installed via this
# bootstrap rather than declared in pyproject.toml (doing so would force
# the whole project's minimum Python up for everyone).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

SDK_VERSION="${HAND_TRACKING_SDK_VERSION:-1.1.0}"
APK_PATH="$REPO_ROOT/assets/hand_tracking_streamer.apk"

echo "[1/2] Installing hand-tracking-sdk==$SDK_VERSION into the active venv"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not on PATH." >&2
    exit 1
fi
uv pip install "hand-tracking-sdk==$SDK_VERSION"

if [ "${SKIP_APK:-0}" = "1" ]; then
    echo "[2/2] SKIP_APK=1, skipping APK install."
    echo "Done (SDK only)."
    exit 0
fi

if [ ! -f "$APK_PATH" ] || [ "$(stat -c '%s' "$APK_PATH")" -lt 100000 ]; then
    echo "ERROR: $APK_PATH is missing or too small; was it checked out?" >&2
    exit 1
fi

if ! command -v adb >/dev/null 2>&1; then
    echo "WARNING: adb not on PATH; cannot install APK. Run 'sudo apt install -y adb' and retry, or pass SKIP_APK=1." >&2
    exit 1
fi

if ! adb devices | awk 'NR>1 && $2=="device"' | grep -q .; then
    echo "ERROR: no Quest authorized via adb. Plug in USB + accept the prompt." >&2
    exit 1
fi

echo "[2/2] Installing APK on connected Quest ($(du -h "$APK_PATH" | cut -f1))"
adb install -r -g "$APK_PATH"

echo ""
echo "Done."
echo "Next steps:"
echo "  1. Put on the headset, launch the hand-tracking-streamer app."
echo "  2. Pick transport (TCP server / localhost:8000 is easiest over USB)."
echo "  3. PC: adb reverse tcp:8000 tcp:8000"
echo "  4. uv run python examples/hand_tracking_print.py"
