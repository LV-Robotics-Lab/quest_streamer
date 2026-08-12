#!/usr/bin/env bash
# Switch between the Quest-side apps we support.
#
# Quest runs exactly one VR-ish app at a time, so using standalone apps
# means the others are paused / torn down. This script just force-stops
# whatever is running and launches the chosen one. It also sets up the
# right `adb forward` / `adb reverse` port for each.
#
# Usage:
#     scripts/switch_mode.sh controller      # in-repo native OpenXR/TCP provider
#     scripts/switch_mode.sh hands           # wengmister/hand-tracking-streamer
#     scripts/switch_mode.sh camera          # in-repo Camera2 streamer activity
#     scripts/switch_mode.sh combined        # Camera2 + controller + hand tracking in one APK
#     scripts/switch_mode.sh stop            # stop them all
#
# Assumes the APKs are already installed on the headset (run the respective
# bootstrap scripts once beforehand).

set -euo pipefail

adb_bin="${QUEST_ADB_BIN:-}"
if [ -z "$adb_bin" ]; then
    adb_bin="$(command -v adb 2>/dev/null || true)"
elif [[ "$adb_bin" != */* ]]; then
    adb_bin="$(command -v "$adb_bin" 2>/dev/null || true)"
fi
if [ -z "$adb_bin" ] || [ ! -x "$adb_bin" ]; then
    echo "ERROR: adb not found; set QUEST_ADB_BIN to one executable." >&2
    exit 1
fi
export QUEST_ADB_BIN="$adb_bin"

adb_cmd() {
    "$adb_bin" "$@"
}

package_for_mode() {
    case "$1" in
        controller|camera|combined) echo "com.rail.oculus.teleop" ;;
        hands) echo "com.wengmister.handtrackingstreamer" ;;
        *) return 2 ;;
    esac
}

activity_for_mode() {
    case "$1" in
        controller|combined) echo "com.rail.oculus.teleop/.MainActivity" ;;
        camera) echo "com.rail.oculus.teleop/com.oculus.camerademo.MainActivity" ;;
        hands) echo "" ;;
        *) return 2 ;;
    esac
}

stop_all() {
    local stop_mode
    for stop_mode in controller hands camera combined; do
        adb_cmd shell am force-stop "$(package_for_mode "$stop_mode")" \
          >/dev/null 2>&1 || true
    done
    adb_cmd forward --remove-all >/dev/null 2>&1 || true
    adb_cmd reverse --remove-all >/dev/null 2>&1 || true
    echo "stopped all quest_streamer apps; cleared adb forward/reverse"
}

check_installed() {
    local pkg="$1"
    local packages
    if ! packages="$(adb_cmd shell pm list packages 2>&1)"; then
        echo "ERROR: adb package query failed using $adb_bin" >&2
        echo "$packages" >&2
        exit 1
    fi
    if ! grep -q "^package:$pkg$" <<<"$packages"; then
        echo "ERROR: $pkg is not installed on the Quest." >&2
        echo "Run the appropriate bootstrap script first." >&2
        exit 1
    fi
}

wire_ports() {
    local mode="$1"
    case "$mode" in
        controller)
            # OpenXR controller frames use a dedicated device-side TCP server.
            adb_cmd forward tcp:9200 tcp:9200 >/dev/null
            echo "adb forward tcp:9200 tcp:9200  (PC -> OpenXR controller server)"
            ;;
        hands)
            # hand-tracking-streamer (APK is TCP client): APK connects to 127.0.0.1:8000
            # on-device, forwarded via adb reverse to PC's 8000.
            adb_cmd reverse tcp:8000 tcp:8000 >/dev/null
            echo "adb reverse tcp:8000 tcp:8000  (APK -> PC)"
            ;;
        camera)
            # quest_camera_streamer (APK is TCP server): PC dials 127.0.0.1:9100,
            # forwarded via adb forward to headset's 9100.
            adb_cmd forward tcp:9100 tcp:9100 >/dev/null
            echo "adb forward tcp:9100 tcp:9100  (PC -> APK)"
            ;;
        combined)
            # in-repo combined APK:
            #   - controller data is served as strict JSONL on device TCP 9200
            #   - hand-tracking-streamer is still a TCP client to the PC on 8000
            #   - embedded Camera2 streamer is a TCP server on the headset on 9100
            adb_cmd reverse tcp:8000 tcp:8000 >/dev/null
            adb_cmd forward tcp:9100 tcp:9100 >/dev/null
            adb_cmd forward tcp:9200 tcp:9200 >/dev/null
            echo "adb reverse tcp:8000 tcp:8000  (combined APK -> PC)"
            echo "adb forward tcp:9100 tcp:9100  (PC -> camera server in APK)"
            echo "adb forward tcp:9200 tcp:9200  (PC -> OpenXR controller server)"
            ;;
    esac
}

launch() {
    local mode="$1"
    local pkg
    local activity
    pkg="$(package_for_mode "$mode")"
    activity="$(activity_for_mode "$mode")"

    check_installed "$pkg"
    stop_all >/dev/null
    wire_ports "$mode"

    if [ -n "$activity" ]; then
      echo "launching $activity"
      local -a start_args=(
        shell am start -n "$activity"
        -a android.intent.action.MAIN
        -c android.intent.category.LAUNCHER
      )
      if [ "$mode" = "combined" ]; then
        start_args+=(
          --ez enable_camera true
          --ez enable_hand_telemetry true
        )
      fi
      adb_cmd "${start_args[@]}" >/dev/null 2>&1 || {
            echo "WARNING: am start returned non-zero; open the app manually from the headset's Unknown Sources library."
        }
    else
        echo "NOTE: $pkg has no well-known launcher activity; start it from the Unknown Sources library inside the headset."
    fi
    echo "active mode: $mode ($pkg)"
}

usage() {
    cat <<EOF
Usage: $0 {controller|hands|camera|combined|stop}

  controller  launch controller-only OpenXR telemetry            (adb forward 9200)
  hands       launch com.wengmister.handtrackingstreamer         (adb reverse 8000)
  camera      launch camera UI in com.rail.oculus.teleop           (adb forward 9100)
  combined    launch controller + optional hands/camera           (ports 8000/9100/9200)
  stop        force-stop all supported apps and clear adb port mappings
EOF
}

if [ $# -ne 1 ]; then usage; exit 2; fi

case "$1" in
    controller|hands|camera|combined) launch "$1" ;;
    stop) stop_all ;;
    -h|--help|help) usage ;;
    *) usage; exit 2 ;;
esac
