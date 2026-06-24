#!/usr/bin/env python3
"""Verify combined APK wire formats with the host-side simulator."""

from __future__ import annotations

import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "scripts" / "simulate_combined_protocols.py"
HAND_PORT = 18000
CAMERA_PORT = 19100
QSTR_HEADER = struct.Struct(">4s c H H I")


def wait_for_hand(result: dict[str, str], ready: threading.Event) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", HAND_PORT))
        server.listen(1)
        server.settimeout(5.0)
        ready.set()
        conn, _ = server.accept()
        with conn:
            conn.settimeout(5.0)
            data = conn.recv(8192).decode("utf-8", errors="replace")
            if "Left wrist:" in data and "Left landmarks:" in data:
                result["hand"] = data.splitlines()[0]


def wait_for_camera() -> str:
    deadline = time.monotonic() + 5.0
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", CAMERA_PORT), timeout=0.5) as sock:
                header = sock.recv(QSTR_HEADER.size)
                magic, side, width, height, jpeg_len = QSTR_HEADER.unpack(header)
                if magic == b"QSTR" and side in (b"L", b"R") and jpeg_len > 0:
                    return f"{side.decode()} {width}x{height} jpeg={jpeg_len}"
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"camera simulator did not produce QSTR frame: {last_error}")


def main() -> int:
    result: dict[str, str] = {}
    hand_ready = threading.Event()
    hand_thread = threading.Thread(target=wait_for_hand, args=(result, hand_ready), daemon=True)
    hand_thread.start()
    if not hand_ready.wait(timeout=2.0):
        print("hand verifier did not bind", file=sys.stderr)
        return 1

    proc = subprocess.Popen(
        [
            sys.executable,
            str(SIM),
            "--duration",
            "5",
            "--hand-port",
            str(HAND_PORT),
            "--camera-port",
            str(CAMERA_PORT),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        controller_line = ""
        deadline = time.monotonic() + 5.0
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if "wE9ryARX: " in line and "&" in line:
                controller_line = line.strip()
                break
        if not controller_line:
            raise RuntimeError("controller simulator did not produce wE9ryARX payload")

        camera = wait_for_camera()
        hand_thread.join(timeout=5.0)
        hand = result.get("hand")
        if not hand:
            raise RuntimeError("hand simulator did not produce wrist + landmarks payload")
    except Exception as exc:
        print(f"simulated protocol verification failed: {exc}", file=sys.stderr)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"controller ok: {controller_line[:120]}")
    print(f"hand ok: {hand}")
    print(f"camera ok: {camera}")
    print("simulated protocol verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
