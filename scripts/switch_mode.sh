#!/usr/bin/env bash
# Switch between the Quest-side apps we support.
#
# Quest runs exactly one VR-ish app at a time, so using standalone apps
# means the others are paused / torn down. This script just force-stops
# whatever is running and launches the chosen one. It also sets up the
# right `adb forward` / `adb reverse` port for each.
#
# Usage:
#     scripts/switch_mode.sh controller      # rail-berkeley/oculus_reader
#     scripts/switch_mode.sh hands           # wengmister/hand-tracking-streamer
#     scripts/switch_mode.sh camera          # in-repo Camera2 streamer activity
#     scripts/switch_mode.sh combined        # Camera2 + controller + hand tracking in one APK
#     scripts/switch_mode.sh stop            # stop them all
#
# Assumes the APKs are already installed on the headset (run the respective
# bootstrap scripts once beforehand).

set -euo pipefail

declare -A PKG
PKG[controller]="com.rail.oculus.teleop"
PKG[hands]="com.wengmister.handtrackingstreamer"
PKG[camera]="com.rail.oculus.teleop"
PKG[combined]="com.rail.oculus.teleop"

declare -A ACTIVITY
ACTIVITY[controller]="com.rail.oculus.teleop/.MainActivity"
ACTIVITY[hands]=""
ACTIVITY[camera]="com.rail.oculus.teleop/com.oculus.camerademo.MainActivity"
ACTIVITY[combined]="com.rail.oculus.teleop/.MainActivity"

stop_all() {
    for mode in controller hands camera combined; do
        adb shell am force-stop "${PKG[$mode]}" >/dev/null 2>&1 || true
    done
    adb forward --remove-all >/dev/null 2>&1 || true
    adb reverse --remove-all >/dev/null 2>&1 || true
    echo "stopped all quest_streamer apps; cleared adb forward/reverse"
}

check_installed() {
    local pkg="$1"
    if ! adb shell pm list packages 2>/dev/null | grep -q "^package:$pkg$"; then
        echo "ERROR: $pkg is not installed on the Quest." >&2
        echo "Run the appropriate bootstrap script first." >&2
        exit 1
    fi
}

wire_ports() {
    local mode="$1"
    case "$mode" in
        controller)
            # oculus_reader talks over adb logcat; no port forwarding.
            ;;
        hands)
            # hand-tracking-streamer (APK is TCP client): APK connects to 127.0.0.1:8000
            # on-device, forwarded via adb reverse to PC's 8000.
            adb reverse tcp:8000 tcp:8000 >/dev/null
            echo "adb reverse tcp:8000 tcp:8000  (APK -> PC)"
            ;;
        camera)
            # quest_camera_streamer (APK is TCP server): PC dials 127.0.0.1:9100,
            # forwarded via adb forward to headset's 9100.
            adb forward tcp:9100 tcp:9100 >/dev/null
            echo "adb forward tcp:9100 tcp:9100  (PC -> APK)"
            ;;
        combined)
            # in-repo combined APK:
            #   - controller data is emitted to logcat in oculus_reader format
            #   - hand-tracking-streamer is still a TCP client to the PC on 8000
            #   - embedded Camera2 streamer is a TCP server on the headset on 9100
            adb reverse tcp:8000 tcp:8000 >/dev/null
            adb forward tcp:9100 tcp:9100 >/dev/null
            echo "adb reverse tcp:8000 tcp:8000  (combined APK -> PC)"
            echo "adb forward tcp:9100 tcp:9100  (PC -> camera server in APK)"
            ;;
    esac
}

launch() {
    local mode="$1"
    local pkg="${PKG[$mode]}"
    local activity="${ACTIVITY[$mode]:-}"

    check_installed "$pkg"
    stop_all >/dev/null
    wire_ports "$mode"

    if [ -n "$activity" ]; then
        echo "launching $activity"
        adb shell am start -n "$activity" >/dev/null 2>&1 || {
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

  controller  launch com.rail.oculus.teleop                     (no port)
  hands       launch com.wengmister.handtrackingstreamer         (adb reverse 8000)
  camera      launch camera UI in com.rail.oculus.teleop           (adb forward 9100)
  combined    launch com.rail.oculus.teleop                      (logcat + adb reverse 8000 + adb forward 9100)
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
