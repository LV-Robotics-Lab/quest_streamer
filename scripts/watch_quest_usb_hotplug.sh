#!/usr/bin/env bash
set -euo pipefail

cat <<'INTRO'
Quest USB hotplug watcher

Use:
  1. Keep this script running.
  2. Unplug the Quest USB-C cable.
  3. Wait 3 seconds.
  4. Plug the Quest directly into a rear motherboard USB port.
  5. Put on the headset and accept any USB / debugging prompt.

Expected for Quest:
  lsusb should show a Meta/Oculus device, usually vendor id 2833.

Press Ctrl-C to stop.
INTRO

echo
echo "== initial lsusb =="
lsusb || true

echo
echo "== current usb sysfs =="
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor" ] || continue
  vendor="$(cat "$d/idVendor" 2>/dev/null || true)"
  product="$(cat "$d/idProduct" 2>/dev/null || true)"
  manufacturer="$(cat "$d/manufacturer" 2>/dev/null || true)"
  name="$(cat "$d/product" 2>/dev/null || true)"
  serial="$(cat "$d/serial" 2>/dev/null || true)"
  printf '%-10s %s:%s  %s | %s | %s\n' "$(basename "$d")" "$vendor" "$product" "$manufacturer" "$name" "$serial"
done

echo
echo "== monitoring udev add/remove events =="
if command -v udevadm >/dev/null 2>&1; then
  udevadm monitor --udev --subsystem-match=usb &
  udev_pid="$!"
else
  udev_pid=""
fi

echo
echo "== monitoring kernel usb messages =="
if dmesg --follow --human --level=err,warn,info 2>/dev/null | grep --line-buffered -iE 'usb|2833|oculus|quest|mtp|adb'; then
  :
else
  echo "dmesg follow was not available without sudo. In another terminal run:"
  echo "  sudo dmesg -wH | grep -iE 'usb|2833|oculus|quest|mtp|adb'"
  if [ -n "${udev_pid:-}" ]; then
    wait "$udev_pid"
  else
    while true; do sleep 3600; done
  fi
fi
