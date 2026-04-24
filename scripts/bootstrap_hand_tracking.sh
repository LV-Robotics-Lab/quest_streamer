#!/usr/bin/env bash
# Bootstrap the hand-tracking pipeline: pip-install the SDK and install the
# companion APK on the connected Quest.
#
# `hand-tracking-sdk` requires Python >=3.12, so we don't declare it in
# pyproject.toml (that would force the whole project's minimum Python up, or
# need per-dep markers). Instead we bootstrap it post-`uv sync`, like we do
# for `oculus_reader`.
#
# Usage:
#     uv sync                                       # create .venv
#     bash scripts/bootstrap_hand_tracking.sh       # populate it
#
# Overrides:
#     QUEST_STREAMER_THIRD_PARTY=/custom/path       # where to clone the upstream
#     SKIP_APK=1                                    # skip `adb install`

set -euo pipefail

THIRD_PARTY_DIR="${QUEST_STREAMER_THIRD_PARTY:-$HOME/third_party}"
STREAMER_REPO_DIR="$THIRD_PARTY_DIR/hand-tracking-streamer"
STREAMER_URL="https://github.com/wengmister/hand-tracking-streamer.git"
APK_REL_PATH="hand_tracking_streamer.apk"

echo "[1/3] Installing hand-tracking-sdk into the active venv"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not on PATH." >&2
    exit 1
fi
uv pip install "hand-tracking-sdk>=1.0,<2.0"

if [ "${SKIP_APK:-0}" = "1" ]; then
    echo "[2/3] SKIP_APK=1 set, skipping APK install."
    echo "[3/3] Done (SDK only)."
    exit 0
fi

if [ ! -d "$STREAMER_REPO_DIR" ]; then
    echo "[2/3] Cloning $STREAMER_URL into $STREAMER_REPO_DIR"
    mkdir -p "$THIRD_PARTY_DIR"
    GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 "$STREAMER_URL" "$STREAMER_REPO_DIR"
else
    echo "[2/3] Using existing checkout at $STREAMER_REPO_DIR"
fi

APK_PATH="$STREAMER_REPO_DIR/$APK_REL_PATH"
if [ ! -f "$APK_PATH" ] || [ "$(stat -c '%s' "$APK_PATH")" -lt 100000 ]; then
    echo "ERROR: APK missing or tiny at $APK_PATH" >&2
    exit 1
fi

if ! command -v adb >/dev/null 2>&1; then
    echo "WARNING: adb not on PATH; cannot install APK. Run 'sudo apt install -y adb' and retry, or pass SKIP_APK=1." >&2
    exit 1
fi

echo "[3/3] Installing APK on connected Quest"
if ! adb devices | awk 'NR>1 && $2=="device"' | grep -q .; then
    echo "ERROR: no Quest authorized via adb. Plug in USB + accept the prompt." >&2
    exit 1
fi

adb install -r -g "$APK_PATH"

echo ""
echo "Done."
echo "Next steps:"
echo "  1. Put on the headset and launch the 'hand-tracking-streamer' app from the Unknown Sources library."
echo "  2. Pick transport mode in the app (TCP server is easiest over USB)."
echo "  3. For wired TCP: run 'adb reverse tcp:8000 tcp:8000' on the PC."
echo "  4. Run: uv run python examples/hand_tracking_print.py"
