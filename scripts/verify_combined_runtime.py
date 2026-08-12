#!/usr/bin/env python3
"""Runtime smoke check for the combined camera + controller + hand Quest APK."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from collections.abc import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from quest_streamer.controller_protocol import (  # noqa: E402
    ControllerProtocolError,
    parse_openxr_controller_packet,
)


PACKAGE = "com.rail.oculus.teleop"
ACTIVITY = "com.rail.oculus.teleop/com.rail.oculus.teleop.MainActivity"
DEFAULT_APK = (
    Path(__file__).resolve().parents[1]
    / "android"
    / "quest_camera_streamer"
    / "app"
    / "build"
    / "outputs"
    / "apk"
    / "debug"
    / "app-debug.apk"
)
ADB_BIN = os.environ.get("QUEST_ADB_BIN") or "adb"


def run_adb(args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ADB_BIN, *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def require_device() -> None:
    proc = run_adb(["devices"], check=True)
    devices = [
        line.split()[0]
        for line in proc.stdout.splitlines()[1:]
        if line.strip().endswith("\tdevice")
    ]
    if not devices:
        raise RuntimeError("no adb device is connected and authorized")


def require_installed() -> None:
    proc = run_adb(["shell", "pm", "list", "packages", PACKAGE], check=True)
    if f"package:{PACKAGE}" not in proc.stdout:
        raise RuntimeError(f"{PACKAGE} is not installed")


def install_apk(apk: Path) -> None:
    if not apk.exists():
        raise RuntimeError(f"APK not found: {apk}")
    run_adb(["install", "-r", "-g", str(apk)], check=True)


def launch_app(*, enable_camera: bool, enable_hand: bool) -> None:
    run_adb(["reverse", "tcp:8000", "tcp:8000"], check=True)
    run_adb(["forward", "tcp:9100", "tcp:9100"], check=False)
    run_adb(["forward", "tcp:9200", "tcp:9200"], check=True)
    run_adb(["logcat", "-c"], check=False)
    proc = run_adb(
        [
            "shell",
            "am",
            "start",
            "-n",
            ACTIVITY,
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "--ez",
            "enable_camera",
            "true" if enable_camera else "false",
            "--ez",
            "enable_hand_telemetry",
            "true" if enable_hand else "false",
        ],
        check=False,
    )
    if proc.returncode != 0:
        run_adb(["shell", "monkey", "-p", PACKAGE, "1"], check=False)


def is_complete_controller_line(line: str) -> bool:
    """Require unique dual controller poses and explicit tracking metadata."""
    marker = "wE9ryARX: "
    if marker not in line:
        return False
    poses, separator, buttons = line.split(marker, 1)[1].partition("&")
    if not separator or "leftTrig" not in buttons or "rightTrig" not in buttons:
        return False

    pose_fields = {}
    for field in poses.split("|"):
        side, side_separator, values = field.partition(":")
        if not side_separator or side not in ("l", "r") or side in pose_fields:
            return False
        try:
            matrix_values = tuple(float(value) for value in values.split())
        except ValueError:
            return False
        if len(matrix_values) != 16 or not all(map(math.isfinite, matrix_values)):
            return False
        pose_fields[side] = matrix_values
    if set(pose_fields) != {"l", "r"}:
        return False

    button_fields = {}
    for field in buttons.split(","):
        parts = field.strip().split()
        if len(parts) == 2:
            if parts[0] in button_fields:
                return False
            button_fields[parts[0]] = parts[1]
    expected_tracking = {
        f"{side}{field}": "1"
        for side in ("left", "right")
        for field in (
            "PoseSource",
            "ControllerActive",
            "PositionTracked",
            "OrientationTracked",
        )
    }
    return all(
        button_fields.get(field) == value
        for field, value in expected_tracking.items()
    )


def wait_for_controller(timeout: float) -> str:
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(
        [ADB_BIN, "logcat", "-T", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            if is_complete_controller_line(line):
                return line.strip()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    raise TimeoutError("timed out waiting for complete left/right controller telemetry")


def controller_tcp_timeout_detail(
    *, connected: bool, complete_frames: int, last_error: Exception | None
) -> str:
    if connected and complete_frames == 0:
        return (
            "TCP connected but no complete frames arrived; "
            "the OpenXR session may be IDLE because the headset is asleep or not worn"
        )
    if complete_frames:
        return f"received {complete_frames} frame(s); last rejection: {last_error}"
    return f"could not connect to the controller server; last error: {last_error}"


def controller_frame_diagnostic(frame: object) -> str:
    states = []
    for side, short in (("left", "l"), ("right", "r")):
        fields = []
        for label, field in (
            ("active", "ControllerActive"),
            ("pos_valid", "PositionValid"),
            ("ori_valid", "OrientationValid"),
            ("pos_tracked", "PositionTracked"),
            ("ori_tracked", "OrientationTracked"),
        ):
            value = getattr(frame, "button_data").get(f"{side}{field}")
            fields.append(f"{label}={1 if value == [1.0] else 0}")
        fields.append(f"pose={1 if short in getattr(frame, 'pose_data') else 0}")
        states.append(f"{side}[{' '.join(fields)}]")
    return " ".join(states)


def validate_controller_frame(frame: object) -> None:
    if getattr(frame, "reference_space") != "local":
        raise RuntimeError(
            "controller TCP frame must use stable local reference space"
        )
    if set(getattr(frame, "pose_data")) != {"l", "r"}:
        raise RuntimeError(
            "controller TCP frame lacks two valid poses: "
            + controller_frame_diagnostic(frame)
        )
    for side in ("left", "right"):
        for field in (
            "ControllerActive",
            "PositionValid",
            "OrientationValid",
            "PositionTracked",
            "OrientationTracked",
        ):
            if getattr(frame, "button_data").get(f"{side}{field}") != [1.0]:
                raise RuntimeError(
                    f"controller TCP frame has invalid {side}{field}: "
                    + controller_frame_diagnostic(frame)
                )


def wait_for_controller_tcp(port: int, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    connected = False
    complete_frames = 0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as sock:
                connected = True
                sock.settimeout(0.5)
                buffer = bytearray()
                while time.monotonic() < deadline:
                    try:
                        chunk = sock.recv(8192)
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        line, _, remainder = buffer.partition(b"\n")
                        buffer = bytearray(remainder)
                        if not line.strip():
                            continue
                        if len(line) > 65536:
                            last_error = RuntimeError(
                                "controller TCP frame exceeds 65536 bytes"
                            )
                            continue
                        complete_frames += 1
                        try:
                            frame = parse_openxr_controller_packet(
                                bytes(line),
                                receive_monotonic_ns=time.monotonic_ns(),
                                generation=complete_frames,
                            )
                            validate_controller_frame(frame)
                        except (RuntimeError, ControllerProtocolError) as exc:
                            # Startup frames commonly precede controller tracking.
                            # Stay on this connection and evaluate subsequent frames.
                            last_error = exc
                            continue
                        return bytes(line).decode("utf-8")
                    if len(buffer) > 65536:
                        raise RuntimeError("controller TCP frame exceeds 65536 bytes")
        except (OSError, RuntimeError, ControllerProtocolError) as exc:
            if complete_frames == 0 or last_error is None:
                last_error = exc
            time.sleep(0.1)
    detail = controller_tcp_timeout_detail(
        connected=connected,
        complete_frames=complete_frames,
        last_error=last_error,
    )
    raise TimeoutError(f"timed out waiting for strict controller TCP frame: {detail}")


def wait_for_hand(port: int, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", port))
        server.listen(1)
        server.settimeout(0.5)
        print(
            f"waiting for hand telemetry on TCP {port}",
            flush=True,
        )
        chunks: list[str] = []
        conn: socket.socket | None = None
        while time.monotonic() < deadline:
            if conn is None:
                try:
                    conn, _ = server.accept()
                    conn.settimeout(0.5)
                except TimeoutError:
                    continue
            try:
                data = conn.recv(4096)
            except TimeoutError:
                continue
            if not data:
                continue
            chunks.append(data.decode("utf-8", errors="replace"))
            text = "".join(chunks)
            if "wrist" in text and "landmarks" in text:
                return text.strip().splitlines()[0]
    raise TimeoutError("timed out waiting for hand wrist + landmarks TCP telemetry")


def wait_for_camera(port: int, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as sock:
                sock.settimeout(0.5)
                chunks = bytearray()
                while time.monotonic() < deadline and len(chunks) < 13:
                    try:
                        data = sock.recv(13 - len(chunks))
                    except TimeoutError:
                        continue
                    if not data:
                        break
                    chunks.extend(data)
                if len(chunks) >= 13 and chunks[:4] == b"QSTR":
                    side = chr(chunks[4])
                    width = int.from_bytes(chunks[5:7], "big")
                    height = int.from_bytes(chunks[7:9], "big")
                    jpeg_size = int.from_bytes(chunks[9:13], "big")
                    return f"side={side} {width}x{height} jpeg={jpeg_size} bytes"
        except OSError as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error is not None:
        raise TimeoutError(f"timed out waiting for camera TCP telemetry: {last_error}")
    raise TimeoutError("timed out waiting for camera TCP telemetry")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--hand-port", type=int, default=8000)
    parser.add_argument("--camera-port", type=int, default=9100)
    parser.add_argument("--controller-port", type=int, default=9200)
    parser.add_argument(
        "--controller-transport",
        choices=("tcp", "logcat"),
        default="tcp",
    )
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--skip-controller", action="store_true")
    parser.add_argument("--skip-hand", action="store_true")
    parser.add_argument("--skip-camera", action="store_true")
    args = parser.parse_args(argv)

    try:
        require_device()
        if args.install:
            install_apk(args.apk)
        require_installed()
        launch_app(
            enable_camera=not args.skip_camera,
            enable_hand=not args.skip_hand,
        )

        if not args.skip_controller:
            if args.controller_transport == "tcp":
                line = wait_for_controller_tcp(args.controller_port, args.timeout)
            else:
                line = wait_for_controller(args.timeout)
            print(f"controller telemetry ok: {line}")

        if not args.skip_hand:
            line = wait_for_hand(args.hand_port, args.timeout)
            print(f"hand telemetry ok: {line}")

        if not args.skip_camera:
            line = wait_for_camera(args.camera_port, args.timeout)
            print(f"camera telemetry ok: {line}")
    except (RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        print(f"combined runtime verification failed: {exc}", file=sys.stderr)
        return 1

    print("combined runtime verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
