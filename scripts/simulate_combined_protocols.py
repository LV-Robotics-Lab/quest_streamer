#!/usr/bin/env python3
"""Host-side protocol simulator for the combined Quest APK outputs.

This is not a VR runtime simulator. It emits the same external wire formats as
the APK so PC-side protocol checks can run without a headset:

* controller: `wE9ryARX: ...` lines on stdout
* hands: TCP client sending HTS-compatible wrist/landmark text to port 8000
* camera: TCP server sending `QSTR` frame headers + JPEG-like payloads on 9100
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import threading
import time
from collections.abc import Sequence


QSTR_HEADER = struct.Struct(">4s c H H I")
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"quest-streamer-sim" * 32 + b"\xff\xd9"


def controller_payload(t: float) -> str:
    lx = 0.08 * math.sin(t)
    rx = 0.08 * math.cos(t)
    left = (
        "l:1.000000 0.000000 0.000000 "
        f"{lx:.6f} 0.000000 1.000000 0.000000 0.020000 "
        "0.000000 0.000000 1.000000 -0.250000 0.000000 0.000000 0.000000 1.000000"
    )
    right = (
        "r:1.000000 0.000000 0.000000 "
        f"{rx:.6f} 0.000000 1.000000 0.000000 -0.020000 "
        "0.000000 0.000000 1.000000 -0.250000 0.000000 0.000000 0.000000 1.000000"
    )
    buttons = (
        "L,X,leftJS 0.100000 -0.200000,leftTrig 0.600000,leftGrip 0.100000,"
        "R,A,rightJS -0.100000 0.200000,rightTrig 0.200000,rightGrip 0.700000"
    )
    return f"{left}|{right}&{buttons}"


def hand_packet(side: str, t: float) -> str:
    x = 0.15 if side == "Right" else -0.15
    wrist = (
        f"{side} wrist:, {x:.4f}, {1.0500 + 0.02 * math.sin(t):.4f}, "
        "0.2500, 0.000, 0.000, 0.000, 1.000"
    )
    points: list[str] = []
    for i in range(21):
        points.extend(
            [
                f"{0.004 * i:.4f}",
                f"{0.002 * (i % 5):.4f}",
                f"{0.020 + 0.004 * (i // 5):.4f}",
            ]
        )
    landmarks = f"{side} landmarks:, " + ", ".join(points)
    return wrist + "\n" + landmarks + "\n"


def run_controller(stop: threading.Event, hz: float) -> None:
    period = 1.0 / hz
    start = time.monotonic()
    while not stop.is_set():
        print(f"wE9ryARX: {controller_payload(time.monotonic() - start)}", flush=True)
        stop.wait(period)


def run_hand_client(stop: threading.Event, host: str, port: int, hz: float) -> None:
    period = 1.0 / hz
    start = time.monotonic()
    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=0.5) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                while not stop.is_set():
                    t = time.monotonic() - start
                    sock.sendall(hand_packet("Left", t).encode("utf-8"))
                    sock.sendall(hand_packet("Right", t).encode("utf-8"))
                    stop.wait(period)
        except OSError:
            stop.wait(0.25)


def run_camera_server(stop: threading.Event, host: str, port: int, hz: float) -> None:
    period = 1.0 / hz
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(4)
        server.settimeout(0.25)
        while not stop.is_set():
            try:
                client, _ = server.accept()
            except TimeoutError:
                continue
            threading.Thread(
                target=serve_camera_client,
                args=(stop, client, period),
                daemon=True,
            ).start()


def serve_camera_client(stop: threading.Event, client: socket.socket, period: float) -> None:
    with client:
        side = b"L"
        while not stop.is_set():
            header = QSTR_HEADER.pack(b"QSTR", side, 1280, 960, len(FAKE_JPEG))
            try:
                client.sendall(header + FAKE_JPEG)
            except OSError:
                return
            side = b"R" if side == b"L" else b"L"
            stop.wait(period)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--hz", type=float, default=30.0)
    parser.add_argument("--hand-host", default="127.0.0.1")
    parser.add_argument("--hand-port", type=int, default=8000)
    parser.add_argument("--camera-host", default="127.0.0.1")
    parser.add_argument("--camera-port", type=int, default=9100)
    args = parser.parse_args(argv)

    stop = threading.Event()
    threads = [
        threading.Thread(target=run_controller, args=(stop, args.hz), daemon=True),
        threading.Thread(
            target=run_hand_client,
            args=(stop, args.hand_host, args.hand_port, args.hz),
            daemon=True,
        ),
        threading.Thread(
            target=run_camera_server,
            args=(stop, args.camera_host, args.camera_port, args.hz),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    try:
        stop.wait(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
