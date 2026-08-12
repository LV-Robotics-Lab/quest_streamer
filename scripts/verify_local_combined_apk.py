#!/usr/bin/env python3
"""Static checks for the in-repo Quest camera + controller + hand APK."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
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
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID_NAME = f"{{{ANDROID_NS}}}name"
ANDROID_VALUE = f"{{{ANDROID_NS}}}value"


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


def component_xmltree_block(xmltree: str, kind: str, component: str) -> str:
    """Return one top-level application component from ``aapt xmltree``."""
    lines = xmltree.splitlines()
    marker = f"      E: {kind} "
    for start, line in enumerate(lines):
        if not line.startswith(marker):
            continue
        end = start + 1
        while end < len(lines) and not lines[end].startswith("      E: "):
            end += 1
        block = "\n".join(lines[start:end])
        if component in block:
            return block
    return ""


def check_apk(apk: Path, aapt: str) -> None:
    require(apk.exists(), f"APK missing: {apk}")

    with zipfile.ZipFile(apk) as zf:
        names = set(zf.namelist())
        native_path = "lib/arm64-v8a/libquesttelemetry.so"
        native_telemetry = zf.read(native_path) if native_path in names else b""
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
        "uses-feature: name='com.oculus.feature.PASSTHROUGH'",
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

    alias_block = component_xmltree_block(
        xmltree, "activity-alias", TELEMETRY_ALIAS
    )
    require(alias_block, f"merged manifest missing alias block {TELEMETRY_ALIAS}")
    require(
        "android.app.lib_name" in alias_block and "questtelemetry" in alias_block,
        "telemetry activity alias must declare android.app.lib_name=questtelemetry",
    )
    for token in (
        b"XR_FB_passthrough",
        b"xrCreatePassthroughFB",
        b"xrCreatePassthroughLayerFB",
        b"OpenXR passthrough composition initialized",
    ):
        require(token in native_telemetry, f"native APK library missing {token!r}")


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
        "xrGetActionStatePose",
        "XR_FB_PASSTHROUGH_EXTENSION_NAME",
        "XrSystemPassthroughPropertiesFB",
        "XrCompositionLayerPassthroughFB",
        "XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB",
        "XR_PASSTHROUGH_LAYER_PURPOSE_RECONSTRUCTION_FB",
        "OpenXR passthrough composition initialized",
        "ANativeActivity_finish(app->activity)",
        "XR_SPACE_LOCATION_POSITION_TRACKED_BIT",
        "XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT",
        "XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING",
        "XR_REFERENCE_SPACE_TYPE_LOCAL",
        'rotateProviderSession("local_reference_space_change")',
        '\\"reference_space\\":\\"local\\"',
        "kHandPort = 8000",
        "kControllerPort = 9200",
        "nero.quest_controller.raw.v1",
        "controllerFrameSeq_",
        "sample_time_ns",
        "leftJS",
        "rightGrip",
    ):
        require(token in cpp, f"native telemetry source missing {token}")
    require(
        "endInfo.layerCount = 0;" not in cpp,
        "native telemetry must not unconditionally submit zero composition layers",
    )
    require(
        "endInfo.layerCount = frameState.shouldRender ? 1u : 0u;" in cpp,
        "native telemetry must submit passthrough whenever OpenXR requests rendering",
    )

    for token in (
        "CameraStreamerViewModel(application)",
        "startStreaming()",
        "requestPermissions",
        "enable_camera",
    ):
        require(token in telemetry_activity, f"TelemetryActivity missing {token}")

    require(TELEMETRY_ALIAS in manifest, f"manifest missing alias {TELEMETRY_ALIAS}")
    root = ET.fromstring(manifest)
    aliases = [
        node
        for node in root.findall("./application/activity-alias")
        if node.get(ANDROID_NAME) == TELEMETRY_ALIAS
    ]
    require(len(aliases) == 1, f"manifest must contain one alias {TELEMETRY_ALIAS}")
    alias_libraries = [
        node.get(ANDROID_VALUE)
        for node in aliases[0].findall("meta-data")
        if node.get(ANDROID_NAME) == "android.app.lib_name"
    ]
    require(
        alias_libraries == ["questtelemetry"],
        "telemetry activity alias must bind NativeActivity to questtelemetry",
    )
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
