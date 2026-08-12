"""Provider-neutral controller reader for ``quest_streamer``.

`OculusReader` returns raw data as two dicts:

    pose_data:   {"l": 4x4 np.ndarray, "r": 4x4 np.ndarray}
    button_data: {
        "leftTrig":  [float],  # [0.0, 1.0]
        "leftGrip":  [float],
        "leftJS":    (x, y),
        "rightTrig": [float],
        "rightGrip": [float],
        "rightJS":   (x, y),
        # plus a handful of discrete buttons: A, B, X, Y, RThU, LThU, ...
    }

`QuestStreamer` keeps that surface but adds:

* a `HandFrame` dataclass for per-hand access
* `read_hand("l" | "r")` that returns `None` when the headset has not produced
  any frames yet (the rwVR code checked ``len(pose_data) == 0`` explicitly)
* `read()` that returns a `RawFrame` wrapping the two raw dicts, for callers
  that want unfiltered access
* optional expression of poses in the Z-up "world" frame defined in
  `quest_streamer.frames`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from quest_streamer.frames import X_QuestWorld, X_WorldQuest
from quest_streamer.controller_protocol import RawFrame
from quest_streamer.controller_provider import (
    ControllerProvider,
    LegacyOculusReaderProvider,
    OpenXRSocketControllerProvider,
)


@dataclass
class HandFrame:
    """Per-hand view of the current Quest state.

    `pose` is 4x4. `trigger` and `grip` are scalars in [0, 1]. `joystick` is a
    2-tuple (x, y) in [-1, 1]. `buttons` carries any remaining discrete
    buttons for that hand ("A", "B" on the right; "X", "Y" on the left; etc.).
    """

    which_hand: str
    pose: np.ndarray
    trigger: float
    grip: float
    joystick: Tuple[float, float]
    buttons: Dict[str, bool]
    pose_source: str = "legacy_unknown"
    controller_active: Optional[bool] = None
    position_valid: Optional[bool] = None
    orientation_valid: Optional[bool] = None
    position_tracked: Optional[bool] = None
    orientation_tracked: Optional[bool] = None


@dataclass(frozen=True)
class HandSignals:
    """Pose-independent controller values preserved from one raw frame.

    OpenXR can keep publishing fresh atomic frames while a controller pose is
    inactive or invalid.  Keeping these fields separate from :class:`HandFrame`
    lets diagnostics report the real action/tracking state without making an
    unavailable pose commandable.
    """

    trigger: float
    grip: float
    joystick: Tuple[float, float]
    buttons: Dict[str, bool]
    pose_source: str
    controller_active: Optional[bool]
    position_valid: Optional[bool]
    orientation_valid: Optional[bool]
    position_tracked: Optional[bool]
    orientation_tracked: Optional[bool]


_HAND_KEYS: Dict[str, Dict[str, str]] = {
    "l": {
        "pose": "l",
        "trigger": "leftTrig",
        "grip": "leftGrip",
        "joystick": "leftJS",
        "pose_source": "leftPoseSource",
        "controller_active": "leftControllerActive",
        "position_valid": "leftPositionValid",
        "orientation_valid": "leftOrientationValid",
        "position_tracked": "leftPositionTracked",
        "orientation_tracked": "leftOrientationTracked",
        # discrete buttons, per oculus_reader.buttons_parser
        "primary": "X",          # X face button
        "secondary": "Y",        # Y face button
        "thumb_rest": "LThU",    # thumb touching the rest pad
        "stick": "LJ",           # joystick clicked in
        "grip_bool": "LG",       # digital grip (SDK-derived)
        "trigger_bool": "LTr",   # digital trigger (SDK-derived)
    },
    "r": {
        "pose": "r",
        "trigger": "rightTrig",
        "grip": "rightGrip",
        "joystick": "rightJS",
        "pose_source": "rightPoseSource",
        "controller_active": "rightControllerActive",
        "position_valid": "rightPositionValid",
        "orientation_valid": "rightOrientationValid",
        "position_tracked": "rightPositionTracked",
        "orientation_tracked": "rightOrientationTracked",
        "primary": "A",          # A face button
        "secondary": "B",        # B face button
        "thumb_rest": "RThU",    # thumb touching the rest pad
        "stick": "RJ",           # joystick clicked in
        "grip_bool": "RG",       # digital grip
        "trigger_bool": "RTr",   # digital trigger
    },
}

_BUTTON_NAMES: Tuple[str, ...] = (
    "primary",
    "secondary",
    "thumb_rest",
    "stick",
    "grip_bool",
    "trigger_bool",
)


def _scalar(value) -> float:
    """Extract a scalar from whatever shape OculusReader hands us."""
    if value is None:
        return 0.0
    if hasattr(value, "__len__"):
        if len(value) == 0:
            return 0.0
        return float(value[0])
    return float(value)


def _pair(value) -> Tuple[float, float]:
    if value is None:
        return (0.0, 0.0)
    if hasattr(value, "__len__") and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return (0.0, 0.0)


def _optional_flag(value) -> Optional[bool]:
    if value is None:
        return None
    return _scalar(value) >= 0.5


def _pose_source(value) -> str:
    if value is None:
        return "legacy_unknown"
    source_code = int(round(_scalar(value)))
    return {
        1: "tracked_remote",
        2: "hand_tracking",
        3: "standard_pointer",
    }.get(source_code, f"unknown_{source_code}")


def hand_frame_from_raw(
    frame: RawFrame,
    which_hand: str,
    in_world_frame: bool = False,
) -> Optional[HandFrame]:
    """Build one hand view from an already captured dual-hand raw frame."""
    if which_hand not in _HAND_KEYS:
        raise ValueError(f"which_hand must be 'l' or 'r', got {which_hand!r}")

    keys = _HAND_KEYS[which_hand]
    pose = frame.pose_data.get(keys["pose"])
    if pose is None:
        return None
    pose = np.asarray(pose, dtype=np.float64)

    if in_world_frame:
        pose = X_QuestWorld @ pose @ X_WorldQuest

    signals = hand_signals_from_raw(frame, which_hand)

    return HandFrame(
        which_hand=which_hand,
        pose=pose,
        trigger=signals.trigger,
        grip=signals.grip,
        joystick=signals.joystick,
        buttons=signals.buttons,
        pose_source=signals.pose_source,
        controller_active=signals.controller_active,
        position_valid=signals.position_valid,
        orientation_valid=signals.orientation_valid,
        position_tracked=signals.position_tracked,
        orientation_tracked=signals.orientation_tracked,
    )


def hand_signals_from_raw(frame: RawFrame, which_hand: str) -> HandSignals:
    """Return controller values and tracking flags even without a valid pose."""
    if which_hand not in _HAND_KEYS:
        raise ValueError(f"which_hand must be 'l' or 'r', got {which_hand!r}")

    keys = _HAND_KEYS[which_hand]
    discrete = {
        name: bool(frame.button_data.get(keys[name], False))
        for name in _BUTTON_NAMES
    }

    return HandSignals(
        trigger=_scalar(frame.button_data.get(keys["trigger"])),
        grip=_scalar(frame.button_data.get(keys["grip"])),
        joystick=_pair(frame.button_data.get(keys["joystick"])),
        buttons=discrete,
        pose_source=_pose_source(frame.button_data.get(keys["pose_source"])),
        controller_active=_optional_flag(
            frame.button_data.get(keys["controller_active"])
        ),
        position_valid=_optional_flag(
            frame.button_data.get(keys["position_valid"])
        ),
        orientation_valid=_optional_flag(
            frame.button_data.get(keys["orientation_valid"])
        ),
        position_tracked=_optional_flag(
            frame.button_data.get(keys["position_tracked"])
        ),
        orientation_tracked=_optional_flag(
            frame.button_data.get(keys["orientation_tracked"])
        ),
    )


class QuestStreamer:
    """Stream pose + button data from a selected controller provider."""

    def __init__(
        self,
        print_fps: bool = False,
        run_oculus_reader: bool = True,
        ip_address: Optional[str] = None,
        port: int = 5555,
        provider: Optional[ControllerProvider] = None,
        backend: str = "openxr-tcp",
        openxr_host: str = "127.0.0.1",
        openxr_port: int = 9200,
    ):
        """Create a streamer.

        Args:
            print_fps: forward OculusReader's own FPS print flag.
            run_oculus_reader: start OculusReader's background thread at init
                time. Set to False if you intend to manage the lifecycle
                manually (e.g. for tests).
            ip_address: if given, use network / WiFi mode. The Quest and the
                PC must be on the same network. On first use OculusReader
                will run `adb tcpip <port>` itself, so the USB cable must
                still be plugged in at least once.
            port: TCP port for legacy adb network mode. Defaults to 5555.
            provider: optional pre-built provider, primarily for tests and
                dependency injection. When set, ``backend`` is ignored.
            backend: ``legacy`` for the diagnostic OculusReader adapter or
                ``openxr-tcp`` for the strict ADB-forwarded socket provider.
            openxr_host/openxr_port: PC endpoint produced by
                ``adb forward tcp:<port> tcp:<port>``.
        """
        if provider is not None:
            self._provider = provider
            self.backend = "injected"
        elif backend == "legacy":
            self._provider = LegacyOculusReaderProvider(
                ip_address=ip_address,
                port=port,
                print_fps=print_fps,
                run=run_oculus_reader,
            )
            self.backend = backend
        elif backend == "openxr-tcp":
            self._provider = OpenXRSocketControllerProvider(
                host=openxr_host,
                port=openxr_port,
            )
            self.backend = backend
        else:
            raise ValueError("backend must be 'legacy' or 'openxr-tcp'")

    # ------------------------------------------------------------------ raw

    def read(self) -> Optional[RawFrame]:
        """Return one unread provider frame, never a cached duplicate."""
        return self._provider.read()

    # ------------------------------------------------------------ per-hand

    def read_hand(self, which_hand: str, in_world_frame: bool = False) -> Optional[HandFrame]:
        """Return a `HandFrame` for `"l"` or `"r"`, or `None` if no data yet.

        Args:
            which_hand: ``"l"`` or ``"r"``.
            in_world_frame: if `True`, the returned `pose` is converted from
                the Quest's native frame into the Z-up "world" frame defined
                in `quest_streamer.frames`. Defaults to `False`, which matches
                what `OculusReader` returns directly.
        """
        frame = self.read()
        if frame is None:
            return None
        return hand_frame_from_raw(frame, which_hand, in_world_frame)

    # ----------------------------------------------------------- lifecycle

    def stop(self) -> None:
        """Stop the underlying provider. Safe to call multiple times."""
        stop_fn = getattr(self._provider, "stop", None)
        if callable(stop_fn):
            stop_fn()

    def __enter__(self) -> "QuestStreamer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
