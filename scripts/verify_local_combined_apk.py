#!/usr/bin/env python3
"""Static checks for the in-repo Quest camera + controller + hand APK."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APK = (
    REPO_ROOT
    / "android"
    / "quest_camera_streamer"
    / "app"
    / "build"
    / "outputs"
    / "apk"
    / "debug"
    / "app-debug.apk"
)
DEFAULT_SOURCE_ROOT = REPO_ROOT / "android" / "quest_camera_streamer"
PACKAGE = "com.rail.oculus.teleop"
TELEMETRY_ALIAS = "com.rail.oculus.teleop.MainActivity"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def find_aapt(source_root: Path) -> str:
    from os import environ

    found = shutil.which("aapt")
    if found:
        return found

    candidates: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = environ.get(key)
        if value:
            candidates.extend(Path(value).glob("build-tools/*/aapt"))

    local_props = source_root / "local.properties"
    if local_props.exists():
        for line in read_text(local_props).splitlines():
            if line.startswith("sdk.dir="):
                sdk = Path(line.split("=", 1)[1].strip())
                candidates.extend(sdk.glob("build-tools/*/aapt"))

    if not candidates:
        raise RuntimeError("aapt not found on PATH, ANDROID_HOME, or local.properties sdk.dir")
    return str(sorted(candidates)[-1])


def run(cmd: Sequence[str]) -> str:
    proc = subprocess.run(
        cmd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_apk(apk: Path, aapt: str) -> None:
    require(apk.exists(), f"APK missing: {apk}")

    with zipfile.ZipFile(apk) as zf:
        names = set(zf.namelist())
    for name in (
        "lib/arm64-v8a/libquesttelemetry.so",
        "lib/arm64-v8a/libopenxr_loader.so",
        "classes.dex",
        "AndroidManifest.xml",
    ):
        require(name in names, f"APK missing {name}")
    unsupported_abis = [
        name
        for name in names
        if name.startswith("lib/")
        and not name.startswith("lib/arm64-v8a/")
        and name.endswith(".so")
    ]
    require(not unsupported_abis, f"APK contains non-arm64 native libs: {unsupported_abis}")

    badging = run([aapt, "dump", "badging", str(apk)])
    require(f"package: name='{PACKAGE}'" in badging, f"package is not {PACKAGE}")
    for token in (
        "uses-permission: name='android.permission.CAMERA'",
        "uses-permission: name='horizonos.permission.HEADSET_CAMERA'",
        "uses-permission: name='com.oculus.permission.HAND_TRACKING'",
        "uses-permission: name='org.khronos.openxr.permission.OPENXR'",
        "uses-feature: name='android.hardware.vr.headtracking'",
        "uses-feature-not-required: name='oculus.software.handtracking'",
    ):
        require(token in badging, f"aapt badging missing {token}")

    xmltree = run([aapt, "dump", "xmltree", str(apk), "AndroidManifest.xml"])
    for token in (
        "com.oculus.camerademo.TelemetryActivity",
        TELEMETRY_ALIAS,
        "android.app.lib_name",
        "questtelemetry",
        "org.khronos.openxr.intent.category.IMMERSIVE_HMD",
        "com.oculus.intent.category.VR",
    ):
        require(token in xmltree, f"merged manifest missing {token}")


def check_sources(source_root: Path) -> None:
    cpp = read_text(source_root / "app/src/main/cpp/quest_telemetry.cpp")
    telemetry_activity = read_text(
        source_root / "app/src/main/java/com/oculus/camerademo/TelemetryActivity.kt"
    )
    manifest = read_text(source_root / "app/src/main/AndroidManifest.xml")
    gradle = read_text(source_root / "app/build.gradle.kts")

    for token in (
        "wE9ryARX",
        "XR_EXT_HAND_TRACKING_EXTENSION_NAME",
        "XR_META_simultaneous_hands_and_controllers",
        "xrLocateHandJointsEXT",
        "XR_ACTION_TYPE_POSE_INPUT",
        "kHandPort = 8000",
        "leftJS",
        "rightGrip",
    ):
        require(token in cpp, f"native telemetry source missing {token}")

    for token in (
        "CameraStreamerViewModel(application)",
        "startStreaming()",
        "requestPermissions",
    ):
        require(token in telemetry_activity, f"TelemetryActivity missing {token}")

    require(TELEMETRY_ALIAS in manifest, f"manifest missing alias {TELEMETRY_ALIAS}")
    require('applicationId = "com.rail.oculus.teleop"' in gradle, "Gradle package mismatch")
    require('abiFilters += "arm64-v8a"' in gradle, "Gradle ABI filter missing")
    require("openxr_loader_for_android" in gradle, "OpenXR loader dependency missing")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    args = parser.parse_args(argv)

    try:
        aapt = find_aapt(args.source_root)
        check_sources(args.source_root)
        check_apk(args.apk, aapt)
    except (RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"local combined APK verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"local combined APK verification passed: {args.apk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
