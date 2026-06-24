#!/usr/bin/env python3
"""Verify that a Unity source tree contains quest_streamer's combined APK hooks."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise AssertionError(f"missing file: {path}") from exc


def project_root(path: pathlib.Path) -> pathlib.Path:
    path = path.resolve()
    if (path / "Assets").is_dir():
        return path
    nested = path / "hand_tracking_streamer"
    if (nested / "Assets").is_dir():
        return nested
    raise AssertionError(
        f"{path} is not a Unity project root or wengmister/hand-tracking-streamer checkout"
    )


def require_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"{label}: missing {needle!r}")


def require_regex(text: str, pattern: str, label: str) -> re.Match[str]:
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise AssertionError(f"{label}: pattern not found: {pattern}")
    return match


def verify_hand_streamer(root: pathlib.Path) -> list[str]:
    path = root / "Assets/Scripts/HandLandmarkStreamer.cs"
    text = read_text(path)

    require_contains(text, "public class HandLandmarkStreamer", "hand streamer")
    require_contains(text, "Oculus.Interaction.Input", "hand streamer")
    require_contains(text, "GetRootPose", "hand wrist pose")
    require_contains(text, "GetJointPosesFromWrist", "hand landmarks")
    require_contains(text, "TcpClient", "hand TCP transport")
    require_contains(text, "UdpClient", "hand UDP transport")
    require_contains(text, "wrist", "hand wrist message")
    require_contains(text, "landmarks", "hand landmarks message")

    joint_match = require_regex(
        text,
        r"private readonly int\[\] _streamedJoints = \{(?P<body>.*?)\};",
        "hand 21-joint list",
    )
    joints = [int(value) for value in re.findall(r"\d+", joint_match.group("body"))]
    if len(joints) != 21:
        raise AssertionError(f"hand streamer: expected 21 streamed joints, found {len(joints)}")

    return [f"hand streamer: {path} streams wrist pose and {len(joints)} joints over TCP/UDP"]


def verify_controller_streamer(root: pathlib.Path) -> list[str]:
    path = root / "Assets/Scripts/OculusReaderControllerStreamer.cs"
    text = read_text(path)

    required = [
        "public sealed class OculusReaderControllerStreamer",
        'LogMarker = "wE9ryARX"',
        "RuntimeInitializeOnLoadMethod",
        "OVRInput.GetConnectedControllers",
        "OVRInput.Controller.LTouch",
        "OVRInput.Controller.RTouch",
        "OVRInput.GetLocalControllerPosition",
        "OVRInput.GetLocalControllerRotation",
        "Debug.Log",
        "AppendButtons",
        "rightJS",
        "leftJS",
        "rightTrig",
        "leftTrig",
        "rightGrip",
        "leftGrip",
    ]
    for needle in required:
        require_contains(text, needle, "controller streamer")

    tokens = ["A", "B", "X", "Y", "RTr", "LTr", "RG", "LG", "RJ", "LJ", "RThU", "LThU"]
    for token in tokens:
        require_contains(text, f'"{token}"', "controller button token")

    return [f"controller streamer: {path} emits oculus_reader-compatible wE9ryARX frames"]


def verify_manifest(root: pathlib.Path) -> list[str]:
    path = root / "Assets/Plugins/Android/AndroidManifest.xml"
    text = read_text(path)

    required = [
        "android.permission.INTERNET",
        "com.oculus.permission.HAND_TRACKING",
        "oculus.software.handtracking",
        "com.unity3d.player.UnityPlayerGameActivity",
        "com.rail.oculus.teleop.MainActivity",
        "android:targetActivity=\"com.unity3d.player.UnityPlayerGameActivity\"",
    ]
    for needle in required:
        require_contains(text, needle, "Android manifest")

    return [f"manifest: {path} declares hand tracking and oculus_reader launch alias"]


def verify_build_script(root: pathlib.Path) -> list[str]:
    path = root / "Assets/Editor/QuestStreamerBuild.cs"
    text = read_text(path)

    require_contains(text, 'ApplicationId = "com.rail.oculus.teleop"', "Unity build script")
    require_contains(text, "quest_controller_hand_streamer.apk", "Unity build output")
    require_contains(text, "BuildPipeline.BuildPlayer", "Unity build script")

    return [f"build script: {path} builds package com.rail.oculus.teleop"]


def verify_camera_bridge(root: pathlib.Path) -> list[str]:
    java_path = root / "Assets/Plugins/Android/QuestCameraStreamer.java"
    bridge_path = root / "Assets/Scripts/QuestCameraStreamerBridge.cs"
    app_manager_path = root / "Assets/Scripts/AppManager.cs"

    java_text = read_text(java_path)
    bridge_text = read_text(bridge_path)
    app_manager_text = read_text(app_manager_path)

    require_contains(java_text, "class QuestCameraStreamer", "optional camera Java bridge")
    require_contains(bridge_text, "QuestCameraStreamerBridge", "optional camera Unity bridge")
    require_contains(app_manager_text, "QuestCameraStreamerBridge.Start", "AppManager camera hook")
    require_contains(app_manager_text, "QuestCameraStreamerBridge.Stop", "AppManager camera hook")

    return [f"optional camera bridge: {java_path} is wired through AppManager"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Unity project root or hand-tracking-streamer checkout")
    parser.add_argument(
        "--skip-camera",
        action="store_true",
        help="do not require the optional quest_streamer Camera2 bridge",
    )
    args = parser.parse_args(argv)

    try:
        root = project_root(pathlib.Path(args.source))
        messages: list[str] = []
        messages.extend(verify_hand_streamer(root))
        messages.extend(verify_controller_streamer(root))
        messages.extend(verify_manifest(root))
        messages.extend(verify_build_script(root))
        if not args.skip_camera:
            messages.extend(verify_camera_bridge(root))
    except AssertionError as exc:
        print(f"combined source verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"combined source verification passed: {root}")
    for message in messages:
        print(f"  - {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
