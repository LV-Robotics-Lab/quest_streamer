"""High-level wrapper for the hand-tracking streamer pipeline.

Parallel to `quest_streamer.wrapper.QuestTeleop` but for bare-hand (finger
joint) tracking via the upstream `hand-tracking-streamer` Android APK and the
`hand-tracking-sdk` Python package.

Unlike the controller wrapper (which reads `adb logcat` through `oculus_reader`),
this one consumes data over a socket:

* ``transport="tcp_server"``: PC listens; works wired over `adb reverse tcp:<port> tcp:<port>`.
* ``transport="tcp_client"``: APK connects to PC at `host:port` over WiFi.
* ``transport="udp"``:        UDP broadcast/unicast; lowest setup, latency-variable.

The upstream SDK ships native data as 21 joint landmark positions plus a 6-DoF
wrist pose in Unity's left-handed frame (X right, Y up, Z forward). This
wrapper exposes both the raw Unity-LH data and a Z-up right-handed "world"
frame (FLU), matching the `pose_world` convention already used by
`QuestTeleop`.

Typical usage:

    from quest_streamer import HandTracker

    with HandTracker(transport="tcp_server", host="0.0.0.0", port=8000) as ht:
        ht.wait_for_ready(timeout=15.0)
        while True:
            snap = ht.snapshot()
            if snap.r.connected and snap.r.wrist_world is not None:
                print(snap.r.wrist_world[:3, 3])         # meters, FLU
                print(snap.r.landmarks_world.shape)       # (21, 3)
            time.sleep(0.05)
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
from scipy.spatial.transform import Rotation as R


# --- Unity-LH -> FLU Z-up world frame -------------------------------------
#
# Match `hand_tracking_sdk.convert.BASIS_UNITY_LEFT_TO_FLU`, which composes
# the Unity-left -> Unity-right handedness flip (diag(1,-1,1)) with a rotation
# from Unity-right (X right, Y up, Z forward) into FLU (X forward, Y left,
# Z up). We apply it here as a single 4x4 similarity transform so that
# `T_world = X_WorldUnity @ T_unity @ X_UnityWorld`, mirroring how the
# controller wrapper uses `X_QuestWorld / X_WorldQuest`.

# BASIS_UNITY_LEFT_TO_FLU as a 3x3 matrix that maps Unity-LH points to FLU
# positions. Includes the Y-axis handedness flip already.
_BASIS_UNITY_LEFT_TO_FLU: np.ndarray = np.array(
    [
        [0.0, 0.0, 1.0],   # FLU x = +Unity z (forward)
        [-1.0, 0.0, 0.0],  # FLU y = -Unity x (left)
        [0.0, 1.0, 0.0],   # FLU z = +Unity y (up) — y-flip already baked in
    ],
    dtype=np.float64,
)

X_WorldUnity: np.ndarray = np.eye(4)
X_WorldUnity[:3, :3] = _BASIS_UNITY_LEFT_TO_FLU
X_UnityWorld: np.ndarray = np.linalg.inv(X_WorldUnity)


def _wrist_pose_to_matrix(pose) -> np.ndarray:
    """Build a 4x4 SE(3) from a `hand_tracking_sdk.WristPose` in Unity-LH."""
    T = np.eye(4)
    T[:3, :3] = R.from_quat([pose.qx, pose.qy, pose.qz, pose.qw]).as_matrix()
    T[:3, 3] = [pose.x, pose.y, pose.z]
    return T


def _landmarks_to_array(landmarks) -> np.ndarray:
    """Convert a `hand_tracking_sdk.HandLandmarks` into a (21, 3) array."""
    return np.asarray(landmarks.points, dtype=np.float64)


# --------------------------------------------------------------- dataclasses

@dataclass
class TrackedHand:
    """Per-hand snapshot for the hand-tracking pipeline.

    Both `wrist` and `wrist_world` are 4x4 homogeneous transforms; `landmarks`
    and `landmarks_world` are `(21, 3)` arrays in the same order as
    `hand_tracking_sdk.STREAMED_JOINT_NAMES` (wrist first, then 4 joints per
    finger in the order thumb/index/middle/ring/little).

    `connected` is `True` once at least one frame has been assembled for this
    hand. All fields are `None` when `connected=False`.
    """

    side: str  # "l" or "r"
    connected: bool = False
    wrist: Optional[np.ndarray] = None
    wrist_world: Optional[np.ndarray] = None
    landmarks: Optional[np.ndarray] = None
    landmarks_world: Optional[np.ndarray] = None
    sequence_id: int = 0
    recv_ts_ns: int = 0
    source_ts_ns: Optional[int] = None
    source_frame_seq: Optional[int] = None
    timestamp: float = 0.0


@dataclass
class HandTrackingSnapshot:
    """Dual-hand hand-tracking snapshot plus loop metadata."""

    l: TrackedHand
    r: TrackedHand
    tick: int
    fps: float
    timestamp: float


StateCallback = Callable[[HandTrackingSnapshot], None]


# --------------------------------------------------------------- main class

class HandTracker:
    """Thread-safe wrapper around `hand_tracking_sdk.HTSClient`.

    Spawns a daemon thread that consumes `HTSClient.iter_events()`, maintains
    the latest `TrackedHand` for each side, and exposes both polling
    (`snapshot()`) and callback (`on_update(cb)`) consumption models.
    """

    # Public coordinate-frame conventions, re-exported for user code.
    X_WorldUnity: np.ndarray = X_WorldUnity
    X_UnityWorld: np.ndarray = X_UnityWorld

    def __init__(
        self,
        transport: str = "tcp_server",
        host: str = "0.0.0.0",
        port: int = 8000,
        timeout_s: float = 1.0,
        reconnect_delay_s: float = 0.25,
        hand_filter: str = "both",
        on_update: Optional[StateCallback] = None,
        start_now: bool = True,
    ) -> None:
        """Create the tracker.

        Args:
            transport: one of ``"tcp_server"``, ``"tcp_client"``, ``"udp"``.
            host: bind (tcp_server/udp) or connect (tcp_client) address.
            port: bind/connect port (default 8000 for TCP, 9000 upstream for UDP).
            timeout_s: I/O timeout applied to the socket loop.
            reconnect_delay_s: delay between tcp_client retries.
            hand_filter: ``"both"``, ``"left"``, ``"right"``.
            on_update: optional per-frame callback. More can be added via
                `on_update()` after construction.
            start_now: start the background thread in ``__init__``.
        """
        try:
            from hand_tracking_sdk import (
                ErrorPolicy,
                HandFilter,
                HTSClient,
                HTSClientConfig,
                StreamOutput,
                TransportMode,
            )
        except ImportError as e:
            raise ImportError(
                "HandTracker requires the `hand-tracking-sdk` package. "
                "Run `scripts/bootstrap_hand_tracking.sh` or "
                "`uv pip install hand-tracking-sdk`."
            ) from e

        # Stash imports for the background loop.
        self._sdk_types = {
            "HandFrame": __import__("hand_tracking_sdk.frame", fromlist=["HandFrame"]).HandFrame,
            "HandSide": __import__("hand_tracking_sdk.models", fromlist=["HandSide"]).HandSide,
        }

        config = HTSClientConfig(
            transport_mode=TransportMode(transport),
            host=host,
            port=port,
            timeout_s=float(timeout_s),
            reconnect_delay_s=float(reconnect_delay_s),
            output=StreamOutput.FRAMES,
            hand_filter=HandFilter(hand_filter),
            error_policy=ErrorPolicy.TOLERANT,  # drop bad lines, keep streaming
            include_wall_time=True,
        )
        self._client = HTSClient(config)

        self._lock = threading.Lock()
        self._latest_l = TrackedHand(side="l")
        self._latest_r = TrackedHand(side="r")
        self._tick = 0
        self._fps_window: List[float] = []

        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._callbacks: List[StateCallback] = []
        if on_update is not None:
            self._callbacks.append(on_update)

        self._last_error: Optional[BaseException] = None

        if start_now:
            self.start()

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start the background ingest thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="HandTracker", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Request the ingest thread to stop.

        Note: the underlying socket can block in receive; the thread is daemonized
        so process shutdown always succeeds, matching upstream `FrameRuntime`.
        """
        self._stop_event.set()

    def __enter__(self) -> "HandTracker":
        if self._thread is None or not self._thread.is_alive():
            self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---------------------------------------------------------- readiness

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Block until the first frame for *either* hand has arrived."""
        return self._ready_event.wait(timeout=timeout)

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    # ------------------------------------------------------- subscription

    def on_update(self, callback: StateCallback) -> StateCallback:
        """Register a callback fired once per ingested frame."""
        with self._lock:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: StateCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # ------------------------------------------------------------ polling

    def snapshot(self) -> HandTrackingSnapshot:
        with self._lock:
            l = self._copy_hand(self._latest_l)
            r = self._copy_hand(self._latest_r)
            now = time.monotonic()
            cutoff = now - 1.0
            while self._fps_window and self._fps_window[0] < cutoff:
                self._fps_window.pop(0)
            fps = float(len(self._fps_window))
            return HandTrackingSnapshot(
                l=l, r=r, tick=self._tick, fps=fps, timestamp=now,
            )

    # ------------------------------------------------------------ the loop

    def _run_loop(self) -> None:
        HandFrame = self._sdk_types["HandFrame"]
        try:
            for event in self._client.iter_events():
                if self._stop_event.is_set():
                    return
                if not isinstance(event, HandFrame):
                    continue
                self._consume_frame(event)
        except Exception as e:  # pragma: no cover - integration path
            self._last_error = e
            traceback.print_exc()

    def _consume_frame(self, frame) -> None:
        HandSide = self._sdk_types["HandSide"]

        wrist_unity = _wrist_pose_to_matrix(frame.wrist)
        wrist_world = X_WorldUnity @ wrist_unity @ X_UnityWorld

        landmarks_unity = _landmarks_to_array(frame.landmarks)
        # transform points: p_world = BASIS @ p_unity
        landmarks_world = (_BASIS_UNITY_LEFT_TO_FLU @ landmarks_unity.T).T

        now = time.monotonic()
        hand = TrackedHand(
            side="l" if frame.side == HandSide.LEFT else "r",
            connected=True,
            wrist=wrist_unity,
            wrist_world=wrist_world,
            landmarks=landmarks_unity,
            landmarks_world=landmarks_world,
            sequence_id=int(frame.sequence_id),
            recv_ts_ns=int(frame.recv_ts_ns),
            source_ts_ns=frame.source_ts_ns,
            source_frame_seq=frame.source_frame_seq,
            timestamp=now,
        )

        with self._lock:
            if hand.side == "l":
                self._latest_l = hand
            else:
                self._latest_r = hand
            self._tick += 1
            self._fps_window.append(now)

            if not self._ready_event.is_set():
                self._ready_event.set()

            callbacks = list(self._callbacks)
            snap = HandTrackingSnapshot(
                l=self._copy_hand(self._latest_l),
                r=self._copy_hand(self._latest_r),
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
    def _copy_hand(h: TrackedHand) -> TrackedHand:
        return TrackedHand(
            side=h.side,
            connected=h.connected,
            wrist=None if h.wrist is None else h.wrist.copy(),
            wrist_world=None if h.wrist_world is None else h.wrist_world.copy(),
            landmarks=None if h.landmarks is None else h.landmarks.copy(),
            landmarks_world=None if h.landmarks_world is None else h.landmarks_world.copy(),
            sequence_id=h.sequence_id,
            recv_ts_ns=h.recv_ts_ns,
            source_ts_ns=h.source_ts_ns,
            source_frame_seq=h.source_frame_seq,
            timestamp=h.timestamp,
        )
