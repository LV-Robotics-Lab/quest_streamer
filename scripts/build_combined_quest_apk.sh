#!/usr/bin/env bash
# Build a single Quest Unity APK that streams both:
#   - Touch controller pose/buttons in rail-berkeley/oculus_reader logcat format
#   - hand/wrist telemetry from wengmister/hand-tracking-streamer
#   - optional passthrough camera MJPEG frames from the local Camera2 plugin

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OVERLAY_DIR="$REPO_ROOT/android/quest_camera_hand_streamer/unity_overlay"
PATCHER="$REPO_ROOT/android/quest_camera_hand_streamer/tools/patch_app_manager.py"
VERIFIER="$REPO_ROOT/android/quest_camera_hand_streamer/tools/verify_combined_source.py"

UPSTREAM_REPO="${HAND_TRACKING_STREAMER_REPO:-https://github.com/wengmister/hand-tracking-streamer.git}"
UPSTREAM_REV="${HAND_TRACKING_STREAMER_REV:-5ff7c1cfea0ead1bb8a0e233bc7770d94d31feb5}"
WORK_ROOT="${QUEST_STREAMER_COMBINED_WORK:-$REPO_ROOT/android/quest_camera_hand_streamer/work}"
UNITY_BIN="${UNITY_BIN:-/Applications/Unity/Hub/Editor/6000.0.38f1/Unity.app/Contents/MacOS/Unity}"
APK_OUT="${QUEST_STREAMER_APK_OUT:-$REPO_ROOT/assets/quest_controller_hand_streamer.apk}"
BUILD_LOG="${QUEST_STREAMER_UNITY_LOG:-$WORK_ROOT/unity-build.log}"

if [[ -n "${QUEST_STREAMER_HAND_SOURCE:-}" ]]; then
    SOURCE_ROOT="$QUEST_STREAMER_HAND_SOURCE"
    echo "using user-supplied hand-tracking-streamer source: $SOURCE_ROOT"
else
    SOURCE_ROOT="$WORK_ROOT/hand-tracking-streamer"
    if [[ ! -d "$SOURCE_ROOT/.git" ]]; then
        mkdir -p "$WORK_ROOT"
        git clone "$UPSTREAM_REPO" "$SOURCE_ROOT"
    fi
    git -C "$SOURCE_ROOT" fetch --depth 1 origin "$UPSTREAM_REV"
    git -C "$SOURCE_ROOT" checkout "$UPSTREAM_REV"
fi

PROJECT_ROOT="$SOURCE_ROOT/hand_tracking_streamer"
APP_MANAGER="$PROJECT_ROOT/Assets/Scripts/AppManager.cs"

if [[ ! -d "$PROJECT_ROOT/Assets" || ! -f "$APP_MANAGER" ]]; then
    echo "ERROR: $SOURCE_ROOT does not look like wengmister/hand-tracking-streamer." >&2
    exit 2
fi
if [[ ! -x "$UNITY_BIN" ]]; then
    echo "ERROR: Unity executable not found: $UNITY_BIN" >&2
    echo "Set UNITY_BIN=/path/to/Unity.app/Contents/MacOS/Unity" >&2
    exit 2
fi

echo "applying quest_streamer combined APK overlay"
rsync -a "$OVERLAY_DIR/" "$PROJECT_ROOT/"
python3 "$PATCHER" "$APP_MANAGER"
python3 "$VERIFIER" "$PROJECT_ROOT"

if [[ "${QUEST_STREAMER_PREPARE_ONLY:-0}" == "1" ]]; then
    cat <<EOF

Prepared combined Unity project:
  $PROJECT_ROOT

The source tree now contains:
  - hand/wrist telemetry from wengmister/hand-tracking-streamer
  - oculus_reader-compatible Touch controller telemetry
  - package/activity compatibility for com.rail.oculus.teleop

Open this project in Unity 6000 with Android Build Support, or rerun without
QUEST_STREAMER_PREPARE_ONLY=1 after activating a Unity Editor license.
EOF
    exit 0
fi

mkdir -p "$(dirname "$APK_OUT")" "$(dirname "$BUILD_LOG")"

echo "building combined APK with Unity"
echo "  project: $PROJECT_ROOT"
echo "  output:  $APK_OUT"
"$UNITY_BIN" \
    -batchmode \
    -quit \
    -projectPath "$PROJECT_ROOT" \
    -executeMethod QuestStreamerBuild.BuildAndroid \
    -logFile "$BUILD_LOG"

if [[ ! -f "$APK_OUT" ]]; then
    echo "ERROR: Unity completed but APK was not created: $APK_OUT" >&2
    echo "Unity log: $BUILD_LOG" >&2
    exit 1
fi

echo "built $APK_OUT"

if [[ "${SKIP_APK_INSTALL:-0}" != "1" ]]; then
    adb install -r "$APK_OUT"
fi

cat <<EOF

Combined mode wiring:
  controller data uses adb logcat marker wE9ryARX (oculus_reader compatible)
  adb reverse tcp:8000 tcp:8000   # hand telemetry: APK -> PC
  adb forward tcp:9100 tcp:9100   # optional camera stream: PC -> APK

Run:
  scripts/switch_mode.sh combined
EOF
