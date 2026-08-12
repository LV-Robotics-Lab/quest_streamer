#!/usr/bin/env bash
set -u

echo "== host =="
uname -a
if [ -f /etc/os-release ]; then
  sed -n '1,8p' /etc/os-release
fi

echo
echo "== user =="
id
groups

echo
echo "== adb =="
if command -v adb >/dev/null 2>&1; then
  command -v adb
  adb version || true
else
  echo "adb not found"
fi

echo
echo "== udev quest rules =="
grep -R "2833\\|Oculus\\|Meta Quest" -n \
  /etc/udev/rules.d \
  /lib/udev/rules.d 2>/dev/null || true

echo
echo "== usb devices =="
if command -v lsusb >/dev/null 2>&1; then
  lsusb || true
else
  echo "lsusb not found"
fi

echo
echo "== adb devices =="
if command -v adb >/dev/null 2>&1; then
  adb kill-server >/dev/null 2>&1 || true
  adb start-server >/dev/null 2>&1 || true
  adb devices -l || true
fi
