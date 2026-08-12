#!/usr/bin/env bash
set -euo pipefail

rule_path="/etc/udev/rules.d/51-meta-quest.rules"
user_name="${SUDO_USER:-${USER:-lv-robotics}}"

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required to install udev rules." >&2
  exit 1
fi

if ! getent group plugdev >/dev/null 2>&1; then
  echo "[quest-usb] creating plugdev group"
  sudo groupadd plugdev
fi

if id "$user_name" >/dev/null 2>&1; then
  echo "[quest-usb] ensuring $user_name is in plugdev"
  sudo usermod -aG plugdev "$user_name"
else
  echo "[quest-usb] warning: user not found: $user_name" >&2
fi

tmp_file="$(mktemp)"
cat >"$tmp_file" <<'RULES'
# Meta/Oculus Quest USB access for Linux ADB / QuestStreamer.
# 2833 is the Meta/Oculus USB vendor id used by Quest headsets.
SUBSYSTEM=="usb", ATTR{idVendor}=="2833", MODE="0666", GROUP="plugdev", TAG+="uaccess"
RULES

echo "[quest-usb] installing $rule_path"
sudo install -m 0644 "$tmp_file" "$rule_path"
rm -f "$tmp_file"

echo "[quest-usb] reloading udev rules"
sudo udevadm control --reload-rules
sudo udevadm trigger

if command -v adb >/dev/null 2>&1; then
  echo "[quest-usb] restarting adb"
  adb kill-server >/dev/null 2>&1 || true
  adb start-server >/dev/null 2>&1 || true
  adb devices -l || true
else
  echo "[quest-usb] adb not found. Install adb before continuing." >&2
fi

cat <<'NEXT'

Next:
  1. Unplug and replug the Quest USB-C cable.
  2. Put on the headset.
  3. Accept "Allow USB debugging"; choose "Always allow from this computer".
  4. Run:
       bash scripts/quest_usb_debug_report.sh

If your current terminal still does not show plugdev in `groups`, open a new
terminal or run `newgrp plugdev`.
NEXT
