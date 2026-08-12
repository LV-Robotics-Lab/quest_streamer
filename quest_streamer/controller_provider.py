"""Controller-provider implementations for :mod:`quest_streamer`."""

from __future__ import annotations

import copy
import socket
import threading
import time
from collections import deque
from typing import Deque, Optional, Protocol, Tuple

import numpy as np

from quest_streamer.controller_protocol import (
    ControllerProtocolError,
    RawFrame,
    parse_openxr_controller_packet,
)


class ControllerProvider(Protocol):
    """Non-blocking source of newly received atomic controller frames."""

    def read(self) -> Optional[RawFrame]:
        """Return one unread frame, or ``None`` when no new frame exists."""

    def stop(self) -> None:
        """Release provider resources."""


class LegacyOculusReaderProvider:
    """Fresh-frame adapter for the historical ``oculus_reader`` package.

    Upstream replaces both cached dictionaries for every parsed logcat line.
    Object identity therefore acts as a receive generation without comparing
    pose values (a stationary controller is still a new frame).  This adapter
    is retained for diagnostics and migration only.
    """

    def __init__(
        self,
        *,
        ip_address: Optional[str] = None,
        port: int = 5555,
        print_fps: bool = False,
        run: bool = True,
        reader: Optional[object] = None,
    ) -> None:
        if reader is None:
            try:
                from oculus_reader.reader import OculusReader
            except ImportError as exc:
                raise ImportError(
                    "legacy controller mode requires the oculus_reader package"
                ) from exc
            reader = OculusReader(
                ip_address=ip_address,
                port=port,
                print_FPS=print_fps,
                run=run,
            )
        self._reader = reader
        # Keep the previous objects alive and compare by identity. Storing only
        # ``id()`` values is unsafe because CPython may reuse an address as soon
        # as an old cache dictionary is released.
        self._last_pose_object: Optional[object] = None
        self._last_button_object: Optional[object] = None
        self._generation = 0
        self._session_id = f"legacy-{id(reader):x}"

    def read(self) -> Optional[RawFrame]:
        pose_data, button_data = self._reader.get_transformations_and_buttons()
        if not pose_data or not button_data:
            return None
        if (
            pose_data is self._last_pose_object
            and button_data is self._last_button_object
        ):
            return None
        self._last_pose_object = pose_data
        self._last_button_object = button_data
        self._generation += 1
        receive_ns = time.monotonic_ns()
        return RawFrame(
            pose_data={
                str(side): np.asarray(pose, dtype=np.float64).copy()
                for side, pose in pose_data.items()
            },
            button_data=copy.deepcopy(button_data),
            provider="legacy_oculus_reader",
            provider_session_id=self._session_id,
            source_frame_seq=self._generation,
            source_sample_time_ns=None,
            receive_monotonic_ns=receive_ns,
            generation=self._generation,
            reference_space="legacy_head",
        )

    def stop(self) -> None:
        stop_fn = getattr(self._reader, "stop", None)
        if callable(stop_fn):
            stop_fn()


class OpenXRSocketControllerProvider:
    """Receive strict OpenXR controller frames over an ADB-forwarded TCP port."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9200,
        *,
        connect_timeout_s: float = 0.5,
        reconnect_delay_s: float = 0.2,
        max_line_bytes: int = 65536,
        queue_size: int = 256,
        start_now: bool = True,
    ) -> None:
        if not host:
            raise ValueError("host must be non-empty")
        if not 0 < port <= 65535:
            raise ValueError("port must be in 1..65535")
        if connect_timeout_s <= 0 or reconnect_delay_s <= 0:
            raise ValueError("timeouts must be > 0")
        if max_line_bytes < 1024:
            raise ValueError("max_line_bytes must be >= 1024")
        if queue_size < 1:
            raise ValueError("queue_size must be >= 1")

        self.host = host
        self.port = int(port)
        self.connect_timeout_s = float(connect_timeout_s)
        self.reconnect_delay_s = float(reconnect_delay_s)
        self.max_line_bytes = int(max_line_bytes)
        self._frames: Deque[RawFrame] = deque(maxlen=int(queue_size))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None
        self._generation = 0
        self._last_source: Optional[Tuple[str, int]] = None
        self._last_error: Optional[BaseException] = None
        self._dropped_frames = 0
        if start_now:
            self.start()

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="OpenXRControllerProvider",
            daemon=True,
        )
        self._thread.start()

    def read(self) -> Optional[RawFrame]:
        if self._last_error is not None:
            raise RuntimeError("OpenXR controller provider failed") from self._last_error
        with self._lock:
            if not self._frames:
                return None
            # Teleoperation needs the freshest state, not an ordered replay of
            # frames that arrived faster than the consumer loop. Returning the
            # oldest frame here can create seconds of hidden control latency.
            frame = self._frames[-1]
            skipped = len(self._frames) - 1
            self._frames.clear()
            self._dropped_frames += skipped
            return frame

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._socket = None

    def _run(self) -> None:
        while not self._stop_event.is_set() and self._last_error is None:
            try:
                with socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout_s
                ) as sock:
                    self._socket = sock
                    sock.settimeout(self.connect_timeout_s)
                    self._read_connection(sock)
            except (ConnectionError, OSError, TimeoutError):
                if not self._stop_event.wait(self.reconnect_delay_s):
                    continue
            finally:
                self._socket = None

    def _read_connection(self, sock: socket.socket) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer.extend(chunk)
            if len(buffer) > self.max_line_bytes and b"\n" not in buffer:
                self._fail(ControllerProtocolError("controller packet exceeds limit"))
                return
            while b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                if not line.strip():
                    continue
                if len(line) > self.max_line_bytes:
                    self._fail(
                        ControllerProtocolError("controller packet exceeds limit")
                    )
                    return
                try:
                    self._accept_line(bytes(line))
                except ControllerProtocolError as exc:
                    self._fail(exc)
                    return

    def _accept_line(self, line: bytes) -> None:
        receive_ns = time.monotonic_ns()
        next_generation = self._generation + 1
        frame = parse_openxr_controller_packet(
            line,
            receive_monotonic_ns=receive_ns,
            generation=next_generation,
        )
        source_key = (frame.provider_session_id, int(frame.source_frame_seq or 0))
        if self._last_source is not None:
            last_session, last_seq = self._last_source
            if source_key[0] == last_session and source_key[1] <= last_seq:
                raise ControllerProtocolError(
                    "frame_seq must increase strictly within a provider session"
                )
        self._last_source = source_key
        self._generation = next_generation
        with self._lock:
            if len(self._frames) == self._frames.maxlen:
                self._dropped_frames += 1
            self._frames.append(frame)

    def _fail(self, error: BaseException) -> None:
        self._last_error = error
        self._stop_event.set()
