#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
vendor_root="$(dirname "$script_dir")"
patch_path="$vendor_root/patches/oculus_reader_controller_identity_tracking.patch"
expected_rev="17bc7b3923f5754d70c4e358867a3bcac1a3c0c3"
source_root="${1:-${OCULUS_READER_SOURCE:-}}"

if [ -z "$source_root" ]; then
  echo "Usage: $0 /path/to/oculus_reader" >&2
  exit 2
fi
if [ ! -d "$source_root/.git" ] || [ ! -f "$source_root/app_source/Src/OculusTeleop.cpp" ]; then
  echo "Not an oculus_reader source checkout: $source_root" >&2
  exit 2
fi

actual_rev="$(git -C "$source_root" rev-parse HEAD)"
if [ "$actual_rev" != "$expected_rev" ]; then
  echo "Expected oculus_reader $expected_rev, found $actual_rev" >&2
  exit 2
fi
if ! git -C "$source_root" diff --quiet || \
   ! git -C "$source_root" diff --cached --quiet; then
  echo "Source checkout must be clean before applying the controller patch." >&2
  exit 2
fi

git -C "$source_root" apply --check "$patch_path"
git -C "$source_root" apply "$patch_path"

cat <<EOF
Prepared patched oculus_reader source at:
  $source_root

The patch restricts emitted l/r poses to tracked Touch remotes and exports
explicit controller-active, position-tracked, and orientation-tracked bits.

Build requirements remain upstream's Oculus Mobile SDK 1.50.0, Android API 26,
and a compatible NDK/Gradle toolchain. Do not deploy until the built APK passes
scripts/verify_combined_runtime.py while both Touch controllers are awake.
EOF
