#!/usr/bin/env python3
"""Runtime smoke check for the combined camera + controller + hand Quest APK."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time
from collections.abc import Sequence


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


def run_adb(args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", *args],
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


def launch_app() -> None:
    run_adb(["reverse", "tcp:8000", "tcp:8000"], check=True)
    run_adb(["forward", "tcp:9100", "tcp:9100"], check=False)
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
        ],
        check=False,
    )
    if proc.returncode != 0:
        run_adb(["shell", "monkey", "-p", PACKAGE, "1"], check=False)


def wait_for_controller(timeout: float) -> str:
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(
        ["adb", "logcat", "-T", "0"],
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
            if "wE9ryARX: " in line and "&" in line:
                return line.strip()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    raise TimeoutError("timed out waiting for controller logcat marker wE9ryARX")


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
        launch_app()

        if not args.skip_controller:
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
