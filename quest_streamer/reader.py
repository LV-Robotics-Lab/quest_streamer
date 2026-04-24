"""Thin wrapper around `oculus_reader.reader.OculusReader`.

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


@dataclass
class RawFrame:
    """Raw data as returned by `OculusReader.get_transformations_and_buttons()`."""

    pose_data: Dict[str, np.ndarray]
    button_data: Dict[str, object]


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


_HAND_KEYS: Dict[str, Dict[str, str]] = {
    "l": {
        "pose": "l",
        "trigger": "leftTrig",
        "grip": "leftGrip",
        "joystick": "leftJS",
        "primary": "X",
        "secondary": "Y",
        "thumbstick": "LThU",
        "js_press": "LJ",
    },
    "r": {
        "pose": "r",
        "trigger": "rightTrig",
        "grip": "rightGrip",
        "joystick": "rightJS",
        "primary": "A",
        "secondary": "B",
        "thumbstick": "RThU",
        "js_press": "RJ",
    },
}


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


class QuestStreamer:
    """Stream pose + button data from a Meta Quest / Oculus controller."""

    def __init__(self, print_fps: bool = False, run_oculus_reader: bool = True):
        try:
            from oculus_reader.reader import OculusReader
        except ImportError as e:
            raise ImportError(
                "quest_streamer depends on the `oculus_reader` package. "
                "Install it from https://github.com/rail-berkeley/oculus_reader "
                "(the same package rwVR uses)."
            ) from e

        self._oculus_reader = OculusReader(
            print_FPS=print_fps,
            run=run_oculus_reader,
        )

    # ------------------------------------------------------------------ raw

    def read(self) -> Optional[RawFrame]:
        """Return the raw pose and button dicts, or `None` if no frame yet."""
        pose_data, button_data = self._oculus_reader.get_transformations_and_buttons()
        if len(pose_data) == 0 or len(button_data) == 0:
            return None
        return RawFrame(pose_data=pose_data, button_data=button_data)

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
        if which_hand not in _HAND_KEYS:
            raise ValueError(f"which_hand must be 'l' or 'r', got {which_hand!r}")

        frame = self.read()
        if frame is None:
            return None

        keys = _HAND_KEYS[which_hand]
        pose = frame.pose_data.get(keys["pose"])
        if pose is None:
            return None
        pose = np.asarray(pose, dtype=np.float64)

        if in_world_frame:
            pose = X_QuestWorld @ pose @ X_WorldQuest

        discrete = {
            name: bool(frame.button_data.get(keys[name], False))
            for name in ("primary", "secondary", "thumbstick", "js_press")
        }

        return HandFrame(
            which_hand=which_hand,
            pose=pose,
            trigger=_scalar(frame.button_data.get(keys["trigger"])),
            grip=_scalar(frame.button_data.get(keys["grip"])),
            joystick=_pair(frame.button_data.get(keys["joystick"])),
            buttons=discrete,
        )

    # ----------------------------------------------------------- lifecycle

    def stop(self) -> None:
        """Stop the underlying OculusReader thread. Safe to call multiple times."""
        stop_fn = getattr(self._oculus_reader, "stop", None)
        if callable(stop_fn):
            stop_fn()

    def __enter__(self) -> "QuestStreamer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
