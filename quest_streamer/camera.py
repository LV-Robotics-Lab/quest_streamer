"""Passthrough-camera stream reader.

Parallel to `QuestTeleop` / `HandTracker`: a background thread pulls JPEG
frames from the on-headset `quest_camera_streamer` APK over TCP, decodes
them to numpy arrays, and exposes both per-frame callbacks and a
thread-safe `snapshot()` that returns the latest frames for each eye.

Wire format is produced by `android/quest_camera_streamer/app/.../StreamingServer.kt`:

    [4 bytes magic "QSTR"]
    [1 byte side:  'L' | 'R' | 'W']   (W = stereo-packed wide; side-split by caller)
    [2 bytes width, 2 bytes height]
    [4 bytes JPEG byte length]
    [JPEG bytes]

Typical wiring for USB use:

    adb forward tcp:9100 tcp:9100

    from quest_streamer import CameraStreamer

    with CameraStreamer(host="127.0.0.1", port=9100) as cam:
        cam.wait_for_ready(timeout=10.0)
        while True:
            snap = cam.snapshot()
            if snap.l.connected:
                cv2.imshow("left", snap.l.frame)
            if snap.r.connected:
                cv2.imshow("right", snap.r.frame)
            cv2.waitKey(1)
"""

from __future__ import annotations

import socket
import struct
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np


_MAGIC = b"QSTR"
_HEADER_STRUCT = struct.Struct(">4s c H H I")  # magic, side, w, h, jpeg_len
_HEADER_LEN = _HEADER_STRUCT.size  # 13


@dataclass
class CameraFrame:
    """Per-eye frame snapshot."""

    side: str  # "l", "r", or "w" (wide stereo-packed; caller responsible for split)
    connected: bool = False
    frame: Optional[np.ndarray] = None  # (H, W, 3) BGR uint8
    width: int = 0
    height: int = 0
    jpeg_bytes: Optional[bytes] = None  # raw JPEG, in case caller wants it
    sequence_id: int = 0
    recv_ts: float = 0.0


@dataclass
class CameraSnapshot:
    """Dual-eye snapshot plus loop metadata."""

    l: CameraFrame
    r: CameraFrame
    w: CameraFrame  # wide stereo-packed; used when APK runs in WIDE mode
    tick: int
    fps: float
    timestamp: float


StateCallback = Callable[[CameraSnapshot], None]


class CameraStreamer:
    """Thread-safe reader for the `quest_camera_streamer` TCP stream.

    Spawns a daemon thread that maintains a socket connection to the APK,
    parses the framed JPEG protocol, decodes each frame with OpenCV (or
    numpy fallback), and exposes polling + callback consumption.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9100,
        decode: bool = True,
        on_update: Optional[StateCallback] = None,
        connect_timeout: float = 5.0,
        reconnect_delay: float = 1.0,
        start_now: bool = True,
    ) -> None:
        """Create the reader.

        Args:
            host: TCP host. Defaults to ``127.0.0.1`` so that
                ``adb forward tcp:<port> tcp:<port>`` routes to the headset.
            port: TCP port. Must match the APK's listening port (default
                9100).
            decode: if True, decode JPEG to BGR uint8 using OpenCV and
                populate ``frame.frame``. If False, only ``frame.jpeg_bytes``
                is populated (save CPU when you want to forward raw).
            on_update: optional callback invoked every frame.
            connect_timeout: socket connect timeout.
            reconnect_delay: delay before retrying connection on failure.
            start_now: start the background thread in ``__init__``.
        """
        self.host = host
        self.port = int(port)
        self.decode = bool(decode)
        self.connect_timeout = float(connect_timeout)
        self.reconnect_delay = float(reconnect_delay)

        if decode:
            try:
                import cv2  # noqa: F401
            except ImportError as e:
                raise ImportError(
                    "CameraStreamer(decode=True) requires opencv-python. "
                    "Either `uv pip install opencv-python` or pass decode=False."
                ) from e

        self._lock = threading.Lock()
        self._latest_l = CameraFrame(side="l")
        self._latest_r = CameraFrame(side="r")
        self._latest_w = CameraFrame(side="w")
        self._tick = 0
        self._fps_window: List[float] = []
        self._seq_counters = {"l": 0, "r": 0, "w": 0}

        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

        self._callbacks: List[StateCallback] = []
        if on_update is not None:
            self._callbacks.append(on_update)

        self._last_error: Optional[BaseException] = None

        if start_now:
            self.start()

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="CameraStreamer", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def __enter__(self) -> "CameraStreamer":
        if self._thread is None or not self._thread.is_alive():
            self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Block until the first frame arrives."""
        return self._ready_event.wait(timeout=timeout)

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    # ------------------------------------------------------- subscription

    def on_update(self, callback: StateCallback) -> StateCallback:
        with self._lock:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: StateCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # ------------------------------------------------------------ polling

    def snapshot(self) -> CameraSnapshot:
        with self._lock:
            now = time.monotonic()
            cutoff = now - 1.0
            while self._fps_window and self._fps_window[0] < cutoff:
                self._fps_window.pop(0)
            fps = float(len(self._fps_window))
            return CameraSnapshot(
                l=self._copy(self._latest_l),
                r=self._copy(self._latest_r),
                w=self._copy(self._latest_w),
                tick=self._tick,
                fps=fps,
                timestamp=now,
            )

    # ------------------------------------------------------------ the loop

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._consume_one_connection()
            except Exception as e:  # pragma: no cover
                self._last_error = e
                traceback.print_exc()
            if self._stop_event.is_set():
                return
            time.sleep(self.reconnect_delay)

    def _consume_one_connection(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.settimeout(None)  # blocking reads after connect
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        try:
            while not self._stop_event.is_set():
                header = _recv_exact(sock, _HEADER_LEN)
                magic, side_b, w, h, jpeg_len = _HEADER_STRUCT.unpack(header)
                if magic != _MAGIC:
                    raise RuntimeError(f"bad magic {magic!r}, stream desync")
                side = side_b.decode("ascii").lower()
                if side not in ("l", "r", "w"):
                    raise RuntimeError(f"unknown side {side!r}")
                if jpeg_len <= 0 or jpeg_len > 50_000_000:
                    raise RuntimeError(f"absurd jpeg length {jpeg_len}")
                jpeg = _recv_exact(sock, jpeg_len)
                self._consume_frame(side, w, h, jpeg)
        finally:
            self._sock = None
            try:
                sock.close()
            except Exception:
                pass

    def _consume_frame(self, side: str, w: int, h: int, jpeg: bytes) -> None:
        frame_arr: Optional[np.ndarray] = None
        if self.decode:
            import cv2
            frame_arr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)

        now = time.monotonic()
        with self._lock:
            self._seq_counters[side] += 1
            cf = CameraFrame(
                side=side,
                connected=True,
                frame=frame_arr,
                width=w,
                height=h,
                jpeg_bytes=jpeg,
                sequence_id=self._seq_counters[side],
                recv_ts=now,
            )
            if side == "l":
                self._latest_l = cf
            elif side == "r":
                self._latest_r = cf
            else:
                self._latest_w = cf
            self._tick += 1
            self._fps_window.append(now)
            if not self._ready_event.is_set():
                self._ready_event.set()
            callbacks = list(self._callbacks)
            snap = CameraSnapshot(
                l=self._copy(self._latest_l),
                r=self._copy(self._latest_r),
                w=self._copy(self._latest_w),
                tick=self._tick,
                fps=float(len(self._fps_window)),
                timestamp=now,
            )
        for cb in callbacks:
            try:
                cb(snap)
            except Exception:
                traceback.print_exc()

    # ---------------------------------------------------------- utilities

    @staticmethod
    def _copy(f: CameraFrame) -> CameraFrame:
        return CameraFrame(
            side=f.side,
            connected=f.connected,
            frame=None if f.frame is None else f.frame.copy(),
            width=f.width,
            height=f.height,
            jpeg_bytes=f.jpeg_bytes,
            sequence_id=f.sequence_id,
            recv_ts=f.recv_ts,
        )


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        chunk = sock.recv_into(view[got:])
        if chunk == 0:
            raise ConnectionError("peer closed mid-frame")
        got += chunk
    return bytes(buf)
