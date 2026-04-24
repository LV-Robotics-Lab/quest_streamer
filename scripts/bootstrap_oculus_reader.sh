#!/usr/bin/env bash
# Bootstrap the controller-side pipeline:
#   1. Clone the `oculus_reader` Python package from GitHub into
#      $QUEST_STREAMER_THIRD_PARTY and pip-install it into the active venv.
#      The package is not on PyPI, so we consume it from source.
#   2. adb-install the companion APK from ./assets/oculus_teleop.apk onto
#      the connected Quest. The APK is vendored in-repo so nothing has to
#      fetch LFS blobs at install time.
#
# Usage:
#     uv sync                                       # create .venv
#     bash scripts/bootstrap_oculus_reader.sh       # install
#
# Overrides:
#     QUEST_STREAMER_THIRD_PARTY=/path              # where to clone the repo
#     OCULUS_READER_REV=<rev|HEAD>                  # upstream git revision
#     SKIP_APK=1                                    # Python package only
#
# The upstream commit is pinned below for reproducibility; bump when needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

THIRD_PARTY_DIR="${QUEST_STREAMER_THIRD_PARTY:-$HOME/third_party}"
REPO_DIR="$THIRD_PARTY_DIR/oculus_reader"
REPO_URL="https://github.com/rail-berkeley/oculus_reader.git"
OCULUS_READER_REV="${OCULUS_READER_REV:-17bc7b3923f5754d70c4e358867a3bcac1a3c0c3}"

APK_PATH="$REPO_ROOT/assets/oculus_teleop.apk"

echo "[1/3] Ensuring oculus_reader checkout at $REPO_DIR"
mkdir -p "$THIRD_PARTY_DIR"
if [ ! -d "$REPO_DIR/.git" ]; then
    # Skip LFS entirely — we vendor the APK ourselves; the clone only needs
    # the Python source. This avoids the need for git-lfs to be installed.
    GIT_LFS_SKIP_SMUDGE=1 git clone "$REPO_URL" "$REPO_DIR" || {
        echo "Initial clone failed (likely because git-lfs is not installed). Retrying without LFS filters..."
        (
            rm -rf "$REPO_DIR"
            git clone --no-checkout "$REPO_URL" "$REPO_DIR"
            cd "$REPO_DIR"
            git config --local filter.lfs.smudge 'cat'
            git config --local filter.lfs.process ''
            git config --local filter.lfs.required false
            git restore --source=HEAD :/
        )
    }
fi

if [ "$OCULUS_READER_REV" != "HEAD" ]; then
    (
        cd "$REPO_DIR"
        current=$(git rev-parse HEAD 2>/dev/null || echo "")
        if [ "$current" != "$OCULUS_READER_REV" ]; then
            echo "      Pinning to ${OCULUS_READER_REV:0:10}"
            git fetch --depth 1 origin "$OCULUS_READER_REV" 2>/dev/null || git fetch origin
            git -c advice.detachedHead=false checkout "$OCULUS_READER_REV"
        fi
    )
fi

echo "[2/3] Installing oculus_reader into active venv"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not on PATH." >&2
    exit 1
fi
uv pip install -e "$REPO_DIR"

if [ "${SKIP_APK:-0}" = "1" ]; then
    echo "[3/3] SKIP_APK=1, skipping APK install."
    echo "Done (Python only)."
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

echo "[3/3] Installing APK on connected Quest ($(du -h "$APK_PATH" | cut -f1))"
adb install -r -g "$APK_PATH"

echo ""
echo "Done."
echo "Next: plug in the Quest, put it on, and run"
echo "    uv run python examples/print_raw_data.py"
