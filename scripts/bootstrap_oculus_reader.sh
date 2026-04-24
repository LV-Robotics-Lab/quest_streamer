#!/usr/bin/env bash
# Bootstrap `oculus_reader` into the active virtualenv.
#
# This is a workaround for the fact that `oculus_reader` is not on PyPI and
# its GitHub repo ships the companion APK via git-lfs - neither `uv add
# git+...` nor `pip install git+...` fetches LFS blobs, so the installed
# package ends up with a 132-byte pointer file where the APK should be.
#
# This script:
#   1. clones the repo (without LFS smudging) into $THIRD_PARTY_DIR
#   2. downloads the real APK via GitHub's LFS media URL
#   3. installs the package into the active venv via `uv pip install -e`
#
# Usage:
#       cd quest_streamer
#       uv sync                                 # creates .venv
#       bash scripts/bootstrap_oculus_reader.sh # fills it in
#
# Override the install location with QUEST_STREAMER_THIRD_PARTY=/some/path
# if you'd rather not have the checkout under $HOME/third_party.

set -euo pipefail

THIRD_PARTY_DIR="${QUEST_STREAMER_THIRD_PARTY:-$HOME/third_party}"
REPO_DIR="$THIRD_PARTY_DIR/oculus_reader"
REPO_URL="https://github.com/rail-berkeley/oculus_reader.git"
APK_MEDIA_URL="https://media.githubusercontent.com/media/rail-berkeley/oculus_reader/main/oculus_reader/APK/teleop-debug.apk"
APK_REL_PATH="oculus_reader/APK/teleop-debug.apk"

echo "[1/4] Ensuring checkout directory exists: $THIRD_PARTY_DIR"
mkdir -p "$THIRD_PARTY_DIR"

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "[2/4] Cloning $REPO_URL into $REPO_DIR (LFS smudge disabled)"
    GIT_LFS_SKIP_SMUDGE=1 git clone "$REPO_URL" "$REPO_DIR" || {
        echo "Initial clone failed (likely because git-lfs is not installed)."
        echo "Retrying with LFS filter forced to a no-op..."
        (
            cd "$REPO_DIR" 2>/dev/null || git clone --no-checkout "$REPO_URL" "$REPO_DIR"
            cd "$REPO_DIR"
            git config --local filter.lfs.smudge 'cat'
            git config --local filter.lfs.process ''
            git config --local filter.lfs.required false
            git restore --source=HEAD :/
        )
    }
else
    echo "[2/4] Using existing checkout at $REPO_DIR"
fi

APK_PATH="$REPO_DIR/$APK_REL_PATH"

is_lfs_pointer=false
if [ -f "$APK_PATH" ]; then
    if head -c 64 "$APK_PATH" | grep -q "git-lfs.github.com/spec"; then
        is_lfs_pointer=true
    fi
fi

if $is_lfs_pointer || [ ! -f "$APK_PATH" ] || [ $(stat -c '%s' "$APK_PATH") -lt 100000 ]; then
    echo "[3/4] APK missing or is an LFS pointer, downloading from $APK_MEDIA_URL"
    mkdir -p "$(dirname "$APK_PATH")"
    curl -fL --retry 3 -o "$APK_PATH" "$APK_MEDIA_URL"
    apk_size=$(stat -c '%s' "$APK_PATH")
    if [ "$apk_size" -lt 100000 ]; then
        echo "ERROR: downloaded APK is only $apk_size bytes; aborting" >&2
        exit 1
    fi
    echo "      Downloaded $apk_size bytes to $APK_PATH"
else
    echo "[3/4] APK already present at $APK_PATH ($(stat -c '%s' "$APK_PATH") bytes)"
fi

echo "[4/4] Installing oculus_reader into active venv"
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not on PATH. Did you activate the env?" >&2
    exit 1
fi
uv pip install -e "$REPO_DIR"

echo ""
echo "Done. oculus_reader + pure-python-adb are available in the active venv."
echo "Next: plug in the Quest, accept the USB-debug prompt, then run:"
echo "    uv run python -c \"from oculus_reader.reader import OculusReader; OculusReader(run=False).install()\""
echo "to push the companion APK to the headset."
