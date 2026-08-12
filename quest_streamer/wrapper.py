"""High-level wrapper around `QuestStreamer`.

`QuestTeleop` is the convenience layer most callers should reach for:

* spawns a background thread that polls the Quest at a fixed frequency,
* maintains a `DeltaPoseTracker` for each hand with a self-managed reference
  pose (engaged_pose stays put across release / re-engage, like rwVR),
* exposes a thread-safe `snapshot()` for polling callers, and an
  `on_update(cb)` subscription for event-driven callers,
* surfaces edge events (`just_engaged`, `just_released`) safely under both
  consumption models,
* provides `wait_for_ready()` so startup code can block until the headset
  is actually producing data.

Polling example:

    with QuestTeleop(frequency=60) as teleop:
        teleop.wait_for_ready(timeout=10.0)
        while True:
            snap = teleop.snapshot()
            if snap.r.engaged:
                command(snap.r.engaged_pose, snap.r.grip)

Callback example:

    def on_update(snap: TeleopSnapshot) -> None:
        if snap.r.just_engaged:
            print("engage!")

    with QuestTeleop(frequency=60, on_update=on_update):
        time.sleep(30.0)
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from quest_streamer.delta_tracker import DeltaPoseTracker, TrackerStep
from quest_streamer.frames import X_QuestWorld, X_WorldQuest
from quest_streamer.reader import (
    HandFrame,
    HandSignals,
    QuestStreamer,
    hand_frame_from_raw,
    hand_signals_from_raw,
)


StateCallback = Callable[["TeleopSnapshot"], None]


@dataclass
class HandState:
    """Per-hand snapshot of the Quest state.

    All poses are 4x4 numpy arrays. `pose` is in the Quest's native frame;
    `pose_world` is the same pose expressed in the Z-up world frame
    defined by `quest_streamer.frames`. `engaged_pose` is the output of the
    internal `DeltaPoseTracker` and lives in the world frame as well.

    `just_engaged` and `just_released` are edge events: `True` only on the
    tick the transition happened, for callback consumers. Polling consumers
    get a "sticky until consumed" version via `QuestTeleop.snapshot()`.
    """

    which_hand: str
    connected: bool = False
    pose: Optional[np.ndarray] = None
    pose_world: Optional[np.ndarray] = None
    trigger: float = 0.0
    grip: float = 0.0
    joystick: Tuple[float, float] = (0.0, 0.0)
    buttons: Dict[str, bool] = field(default_factory=dict)
    engaged: bool = False
    engaged_pose: Optional[np.ndarray] = None
    just_engaged: bool = False
    just_released: bool = False
    timestamp: float = 0.0
    pose_source: str = "legacy_unknown"
    controller_active: Optional[bool] = None
    position_valid: Optional[bool] = None
    orientation_valid: Optional[bool] = None
    position_tracked: Optional[bool] = None
    orientation_tracked: Optional[bool] = None


@dataclass
class TeleopSnapshot:
    """Dual-hand teleop snapshot, plus loop metadata."""

    l: HandState
    r: HandState
    tick: int
    fps: float
    timestamp: float
    provider: str = "legacy_unknown"
    provider_session_id: str = ""
    source_frame_seq: Optional[int] = None
    source_sample_time_ns: Optional[int] = None
    receive_monotonic_ns: int = 0
    reference_space: str = "legacy_unknown"
    source_stale: bool = False


class QuestTeleop:
    """Complete, thread-safe wrapper around `QuestStreamer` for dual-hand use."""

    def __init__(
        self,
        frequency: float = 60.0,
        translation_scaling: Optional[Dict[str, float]] = None,
        trigger_threshold: float = 0.5,
        streamer: Optional[QuestStreamer] = None,
        on_update: Optional[StateCallback] = None,
        start_now: bool = True,
        ip_address: Optional[str] = None,
        port: int = 5555,
        controller_backend: str = "openxr-tcp",
        openxr_host: str = "127.0.0.1",
        openxr_port: int = 9200,
        source_stale_timeout_s: float = 0.3,
    ) -> None:
        """Create the teleop wrapper.

        Args:
            frequency: bg-loop rate in Hz.
            translation_scaling: per-hand scale factor for delta translation.
            trigger_threshold: trigger value above which a hand is "engaged".
            streamer: pre-built `QuestStreamer`. If given, `ip_address`/`port`
                are ignored (the caller already configured the transport).
            on_update: optional callback invoked every tick.
            start_now: start the bg thread in `__init__`.
            ip_address: if set and `streamer is None`, switch the internally
                created streamer to network mode using this Quest IP.
            port: adb TCP port for network mode. Defaults to 5555.
            controller_backend: ``legacy`` or ``openxr-tcp`` when this class
                owns the streamer.
            openxr_host/openxr_port: ADB-forwarded OpenXR provider endpoint.
            source_stale_timeout_s: mark the local snapshot disconnected after
                this interval without a newly received provider frame.
        """
        if frequency <= 0:
            raise ValueError(f"frequency must be > 0, got {frequency}")
        if source_stale_timeout_s <= 0:
            raise ValueError("source_stale_timeout_s must be > 0")

        self.frequency = float(frequency)
        self._dt = 1.0 / self.frequency
        self.trigger_threshold = float(trigger_threshold)
        self.source_stale_timeout_s = float(source_stale_timeout_s)
        self._source_stale_timeout_ns = int(source_stale_timeout_s * 1e9)

        scaling = {"l": 1.0, "r": 1.0}
        if translation_scaling is not None:
            scaling.update(translation_scaling)
        self._scaling = scaling

        self._owned_streamer = streamer is None
        if streamer is not None:
            self._streamer = streamer
        else:
            self._streamer = QuestStreamer(
                ip_address=ip_address,
                port=port,
                backend=controller_backend,
                openxr_host=openxr_host,
                openxr_port=openxr_port,
            )

        self._trackers: Dict[str, DeltaPoseTracker] = {
            hand: DeltaPoseTracker(
                streamer=self._streamer,
                which_hand=hand,
                translation_scaling_factor=self._scaling[hand],
                trigger_threshold=self.trigger_threshold,
            )
            for hand in ("l", "r")
        }
        self._X_WorldRef: Dict[str, np.ndarray] = {
            "l": np.eye(4),
            "r": np.eye(4),
        }

        self._lock = threading.Lock()
        self._latest: TeleopSnapshot = TeleopSnapshot(
            l=HandState(which_hand="l"),
            r=HandState(which_hand="r"),
            tick=0,
            fps=0.0,
            timestamp=0.0,
        )
        self._sticky: Dict[str, Dict[str, bool]] = {
            "l": {"engaged": False, "released": False},
            "r": {"engaged": False, "released": False},
        }
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._callbacks: List[StateCallback] = []
        if on_update is not None:
            self._callbacks.append(on_update)

        self._last_error: Optional[BaseException] = None
        self._fps_window: List[float] = []
        self._last_receive_monotonic_ns = 0
        self._last_source_tick = 0
        self._next_source_tick = 0
        self._last_provider_session_id = ""
        self._source_stale = False
        self._release_required_after_stale: Dict[str, bool] = {
            "l": False,
            "r": False,
        }

        if start_now:
            self.start()

    # ---------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Start the background polling thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="QuestTeleop", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the polling thread and the underlying streamer (idempotent)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._owned_streamer:
            try:
                self._streamer.stop()
            except Exception:
                pass

    def __enter__(self) -> "QuestTeleop":
        if self._thread is None or not self._thread.is_alive():
            self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---------------------------------------------------------- readiness

    def wait_for_ready(self, timeout: float = 5.0) -> bool:
        """Block until both hands have produced at least one valid frame.

        Returns True on success, False on timeout.
        """
        return self._ready_event.wait(timeout=timeout)

    @property
    def last_error(self) -> Optional[BaseException]:
        """The last exception raised by the background loop, if any."""
        return self._last_error

    # ------------------------------------------------------- subscription

    def on_update(self, callback: StateCallback) -> StateCallback:
        """Register a callback invoked (outside the lock) on every tick.

        Returns the callback so it can be used as a decorator.
        """
        with self._lock:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: StateCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # ------------------------------------------------------------- polling

    def snapshot(self) -> TeleopSnapshot:
        """Return the latest state with sticky edge events merged in and cleared.

        Edge flags (`just_engaged`, `just_released`) are `True` if the event
        fired at any point since the previous `snapshot()` call. This makes
        polling safe even when the caller's loop is slower than `frequency`.
        """
        with self._lock:
            snap = self._copy_snapshot_locked()
            for hand in ("l", "r"):
                target: HandState = getattr(snap, hand)
                target.just_engaged = target.just_engaged or self._sticky[hand]["engaged"]
                target.just_released = target.just_released or self._sticky[hand]["released"]
                self._sticky[hand]["engaged"] = False
                self._sticky[hand]["released"] = False
            return snap

    # ----------------------------------------------------------- control

    def set_reference_pose(self, hand: str, X_WorldRef: np.ndarray) -> None:
        """Override the world-frame reference pose used by a hand's tracker.

        On the next engage, the delta tracker will snapshot this pose (rather
        than the previous engaged pose) as the origin of delta motion. Safe
        to call from any thread.
        """
        if hand not in ("l", "r"):
            raise ValueError(f"hand must be 'l' or 'r', got {hand!r}")
        pose = np.asarray(X_WorldRef, dtype=np.float64)
        if pose.shape != (4, 4):
            raise ValueError(f"X_WorldRef must be 4x4, got shape {pose.shape}")
        with self._lock:
            self._X_WorldRef[hand] = pose.copy()
            self._trackers[hand].reset()

    def reset(self, hand: Optional[str] = None) -> None:
        """Force a fresh engage on the next trigger press for one or both hands."""
        hands = ("l", "r") if hand is None else (hand,)
        for h in hands:
            if h not in ("l", "r"):
                raise ValueError(f"hand must be 'l' or 'r', got {h!r}")
        with self._lock:
            for h in hands:
                self._trackers[h].reset()

    def set_translation_scaling(self, hand: str, scale: float) -> None:
        """Change the translation scaling factor for a hand at runtime."""
        if hand not in ("l", "r"):
            raise ValueError(f"hand must be 'l' or 'r', got {hand!r}")
        with self._lock:
            self._scaling[hand] = float(scale)
            self._trackers[hand].translation_scaling_factor = float(scale)

    # ------------------------------------------------------------ the loop

    def _run_loop(self) -> None:
        tick = 0
        t_start = time.monotonic()
        try:
            while not self._stop_event.is_set():
                t_cycle_end = t_start + (tick + 1) * self._dt

                try:
                    snap, callbacks = self._tick(tick, t_cycle_end)
                except Exception as e:  # background-thread errors must be visible
                    self._last_error = e
                    traceback.print_exc()
                    snap, callbacks = None, []

                if snap is not None:
                    for cb in callbacks:
                        try:
                            cb(snap)
                        except Exception:
                            traceback.print_exc()

                tick += 1
                self._precise_wait(t_cycle_end)
        finally:
            pass

    def _tick(
        self,
        tick: int,
        t_cycle_end: float,
    ) -> Tuple[Optional[TeleopSnapshot], List[StateCallback]]:
        poll_now = time.monotonic()
        try:
            raw_frame = self._streamer.read()
        except Exception as error:
            # A strict provider can fail permanently on a malformed frame.
            # Treat that exactly like loss of fresh input so polling callers
            # cannot keep seeing the last connected snapshot indefinitely.
            self._last_error = error
            return self._mark_source_stale_if_needed(poll_now)
        if raw_frame is None:
            return self._mark_source_stale_if_needed(poll_now)

        receive_ns = int(raw_frame.receive_monotonic_ns or time.monotonic_ns())
        now = receive_ns / 1_000_000_000.0
        provider_session_changed = (
            bool(self._last_provider_session_id)
            and raw_frame.provider_session_id != self._last_provider_session_id
        )
        if provider_session_changed:
            self._next_source_tick = 0
        # This is the sequence of fresh frames actually consumed by the host.
        # The higher-rate Quest frame number remains in source_frame_seq; using
        # it here would misclassify intentional newest-only downsampling as V1
        # source gaps.
        source_tick = self._next_source_tick
        self._next_source_tick += 1
        self._last_receive_monotonic_ns = receive_ns
        self._last_source_tick = int(source_tick)
        self._last_provider_session_id = raw_frame.provider_session_id
        self._source_stale = False

        if provider_session_changed:
            # A new Quest process invalidates both delta-pose references even
            # when TCP reconnects faster than the stale timeout. Never let a
            # Trigger held across that boundary auto-engage the new session.
            self._ready_event.clear()
            for hand in ("l", "r"):
                self._trackers[hand].reset()
                self._release_required_after_stale[hand] = True

        l_frame = hand_frame_from_raw(raw_frame, "l")
        r_frame = hand_frame_from_raw(raw_frame, "r")
        frames = {"l": l_frame, "r": r_frame}
        signals = {
            "l": hand_signals_from_raw(raw_frame, "l"),
            "r": hand_signals_from_raw(raw_frame, "r"),
        }
        steps: Dict[str, Optional[TrackerStep]] = {}
        for hand in ("l", "r"):
            frame = frames[hand]
            if not self._frame_is_commandable(raw_frame.provider, frame):
                # Fresh provider traffic is not the same as a commandable
                # controller.  Lose the delta reference immediately and make
                # recovery release-gated just like transport staleness.
                self._trackers[hand].reset()
                self._release_required_after_stale[hand] = True
                steps[hand] = None
            elif self._release_required_after_stale[hand]:
                if frame is not None and frame.trigger <= self.trigger_threshold:
                    self._release_required_after_stale[hand] = False
                    self._trackers[hand].reset()
                steps[hand] = None
            else:
                steps[hand] = self._step_hand(hand, frame)

        l_state = self._compose_state("l", l_frame, signals["l"], steps["l"], now)
        r_state = self._compose_state("r", r_frame, signals["r"], steps["r"], now)

        with self._lock:
            self._fps_window.append(now)
            cutoff = now - 1.0
            while self._fps_window and self._fps_window[0] < cutoff:
                self._fps_window.pop(0)
            fps = float(len(self._fps_window))

            self._latest = TeleopSnapshot(
                l=l_state,
                r=r_state,
                tick=int(source_tick),
                fps=fps,
                timestamp=now,
                provider=raw_frame.provider,
                provider_session_id=raw_frame.provider_session_id,
                source_frame_seq=raw_frame.source_frame_seq,
                source_sample_time_ns=raw_frame.source_sample_time_ns,
                receive_monotonic_ns=receive_ns,
                reference_space=raw_frame.reference_space,
                source_stale=False,
            )

            if l_state.just_engaged:
                self._sticky["l"]["engaged"] = True
            if l_state.just_released:
                self._sticky["l"]["released"] = True
            if r_state.just_engaged:
                self._sticky["r"]["engaged"] = True
            if r_state.just_released:
                self._sticky["r"]["released"] = True

            if (
                self._frame_is_commandable(raw_frame.provider, l_frame)
                and self._frame_is_commandable(raw_frame.provider, r_frame)
            ):
                self._ready_event.set()
            else:
                self._ready_event.clear()

            callbacks = list(self._callbacks)
            snap = self._copy_snapshot_locked()
        return snap, callbacks

    def _mark_source_stale_if_needed(
        self,
        now: float,
    ) -> Tuple[Optional[TeleopSnapshot], List[StateCallback]]:
        if self._last_receive_monotonic_ns <= 0 or self._source_stale:
            return None, []
        now_ns = time.monotonic_ns()
        if now_ns - self._last_receive_monotonic_ns <= self._source_stale_timeout_ns:
            return None, []

        self._source_stale = True
        self._ready_event.clear()
        for hand in ("l", "r"):
            self._trackers[hand].reset()
            self._release_required_after_stale[hand] = True

        l_state = self._compose_state("l", None, None, None, now)
        r_state = self._compose_state("r", None, None, None, now)
        with self._lock:
            previous = self._latest
            self._latest = TeleopSnapshot(
                l=l_state,
                r=r_state,
                tick=self._last_source_tick,
                fps=0.0,
                timestamp=now,
                provider=previous.provider,
                provider_session_id=previous.provider_session_id,
                source_frame_seq=previous.source_frame_seq,
                source_sample_time_ns=previous.source_sample_time_ns,
                receive_monotonic_ns=self._last_receive_monotonic_ns,
                reference_space=previous.reference_space,
                source_stale=True,
            )
            callbacks = list(self._callbacks)
            snap = self._copy_snapshot_locked()
        return snap, callbacks

    def _step_hand(self, hand: str, frame: Optional[HandFrame]) -> Optional[TrackerStep]:
        if frame is None:
            return None
        with self._lock:
            X_ref = self._X_WorldRef[hand].copy()
            tracker = self._trackers[hand]
        step = tracker.step_frame(frame, X_WorldRef_current=X_ref)
        if step is not None and not step.just_released:
            with self._lock:
                self._X_WorldRef[hand] = step.X_WorldRef_next.copy()
        return step

    @staticmethod
    def _frame_is_commandable(provider: str, frame: Optional[HandFrame]) -> bool:
        if frame is None:
            return False
        if provider != "openxr_tcp":
            return True
        return (
            frame.pose_source == "tracked_remote"
            and frame.controller_active is True
            and frame.position_valid is True
            and frame.orientation_valid is True
            and frame.position_tracked is True
            and frame.orientation_tracked is True
        )

    def _compose_state(
        self,
        hand: str,
        frame: Optional[HandFrame],
        signals: Optional[HandSignals],
        step: Optional[TrackerStep],
        now: float,
    ) -> HandState:
        state = HandState(which_hand=hand, timestamp=now)
        if signals is not None:
            state.trigger = signals.trigger
            state.grip = signals.grip
            state.joystick = signals.joystick
            state.buttons = dict(signals.buttons)
            state.pose_source = signals.pose_source
            state.controller_active = signals.controller_active
            state.position_valid = signals.position_valid
            state.orientation_valid = signals.orientation_valid
            state.position_tracked = signals.position_tracked
            state.orientation_tracked = signals.orientation_tracked
        if frame is None:
            with self._lock:
                state.engaged_pose = self._X_WorldRef[hand].copy()
            return state

        state.connected = True
        state.pose = frame.pose.copy()
        state.pose_world = X_QuestWorld @ frame.pose @ X_WorldQuest

        if step is None:
            state.engaged = False
            with self._lock:
                state.engaged_pose = self._X_WorldRef[hand].copy()
        else:
            state.engaged = not step.just_released
            state.engaged_pose = step.X_WorldRef_next.copy()
            state.just_engaged = step.just_engaged
            state.just_released = step.just_released
        return state

    def _copy_snapshot_locked(self) -> TeleopSnapshot:
        """Return a shallow copy of `self._latest` with fresh HandState copies.

        Caller must hold `self._lock`.
        """
        src = self._latest
        return TeleopSnapshot(
            l=self._copy_hand_state(src.l),
            r=self._copy_hand_state(src.r),
            tick=src.tick,
            fps=src.fps,
            timestamp=src.timestamp,
            provider=src.provider,
            provider_session_id=src.provider_session_id,
            source_frame_seq=src.source_frame_seq,
            source_sample_time_ns=src.source_sample_time_ns,
            receive_monotonic_ns=src.receive_monotonic_ns,
            reference_space=src.reference_space,
            source_stale=src.source_stale,
        )

    @staticmethod
    def _copy_hand_state(s: HandState) -> HandState:
        return HandState(
            which_hand=s.which_hand,
            connected=s.connected,
            pose=None if s.pose is None else s.pose.copy(),
            pose_world=None if s.pose_world is None else s.pose_world.copy(),
            trigger=s.trigger,
            grip=s.grip,
            joystick=s.joystick,
            buttons=dict(s.buttons),
            engaged=s.engaged,
            engaged_pose=None if s.engaged_pose is None else s.engaged_pose.copy(),
            just_engaged=s.just_engaged,
            just_released=s.just_released,
            timestamp=s.timestamp,
            pose_source=s.pose_source,
            controller_active=s.controller_active,
            position_valid=s.position_valid,
            orientation_valid=s.orientation_valid,
            position_tracked=s.position_tracked,
            orientation_tracked=s.orientation_tracked,
        )

    @staticmethod
    def _precise_wait(t_end: float, slack: float = 0.001) -> None:
        now = time.monotonic()
        remaining = t_end - now
        if remaining > slack:
            time.sleep(remaining - slack)
        while time.monotonic() < t_end:
            pass
