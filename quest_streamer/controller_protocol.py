"""Strict raw-controller protocol shared by Quest controller providers.

The public ``QuestTeleop`` API intentionally consumes ``RawFrame`` objects
instead of knowing whether a frame came from the historical logcat APK or the
new OpenXR socket provider.  Provider timestamps are kept separate from the
host receive clock because ``XrTime`` and ``time.monotonic_ns()`` do not share a
clock domain.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

OPENXR_CONTROLLER_SCHEMA = "nero.quest_controller.raw.v1"
SIDES: Tuple[str, str] = ("left", "right")


class ControllerProtocolError(ValueError):
    """Raised when a raw controller frame violates the provider contract."""


@dataclass
class RawFrame:
    """One atomic dual-controller frame plus transport provenance.

    Defaults preserve compatibility with callers that construct legacy
    ``RawFrame(pose_data=..., button_data=...)`` values in tests or replay
    helpers.  Production providers must populate ``generation`` and
    ``receive_monotonic_ns`` for every newly received source frame.
    """

    pose_data: Dict[str, np.ndarray]
    button_data: Dict[str, object]
    provider: str = "legacy_unknown"
    provider_session_id: str = ""
    source_frame_seq: Optional[int] = None
    source_sample_time_ns: Optional[int] = None
    receive_monotonic_ns: int = 0
    generation: int = 0
    reference_space: str = "legacy_unknown"


def _duplicate_rejecting_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControllerProtocolError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ControllerProtocolError(f"{field} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, object], expected: Sequence[str], field: str
) -> None:
    actual = set(value)
    expected_set = set(expected)
    missing = sorted(expected_set - actual)
    unknown = sorted(actual - expected_set)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise ControllerProtocolError(f"{field} fields are invalid ({'; '.join(details)})")


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ControllerProtocolError(f"{field} must be a boolean")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ControllerProtocolError(f"{field} must be an integer >= 0")
    return value


def _number(
    value: object,
    field: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControllerProtocolError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ControllerProtocolError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise ControllerProtocolError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ControllerProtocolError(f"{field} must be <= {maximum}")
    return result


def _pose(value: object, field: str) -> Optional[np.ndarray]:
    if value is None:
        return None
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 16
    ):
        raise ControllerProtocolError(f"{field} must contain 16 values or null")
    flat = [_number(item, f"{field}[{index}]") for index, item in enumerate(value)]
    matrix = np.asarray(flat, dtype=np.float64).reshape((4, 4))
    if not np.allclose(matrix[3], (0.0, 0.0, 0.0, 1.0), atol=1e-5):
        raise ControllerProtocolError(f"{field} has an invalid homogeneous row")
    return matrix


def _joystick(value: object, field: str) -> Tuple[float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 2
    ):
        raise ControllerProtocolError(f"{field} must contain two values")
    return (
        _number(value[0], f"{field}[0]", minimum=-1.0, maximum=1.0),
        _number(value[1], f"{field}[1]", minimum=-1.0, maximum=1.0),
    )


def _button_tokens(side: str, values: Mapping[str, object]) -> Dict[str, bool]:
    expected = ("primary", "secondary", "thumb_rest", "stick")
    _exact_fields(values, expected, f"hands.{side}.buttons")
    parsed = {
        key: _boolean(values[key], f"hands.{side}.buttons.{key}")
        for key in expected
    }
    if side == "left":
        return {
            "X": parsed["primary"],
            "Y": parsed["secondary"],
            "LThU": parsed["thumb_rest"],
            "LJ": parsed["stick"],
        }
    return {
        "A": parsed["primary"],
        "B": parsed["secondary"],
        "RThU": parsed["thumb_rest"],
        "RJ": parsed["stick"],
    }


def parse_openxr_controller_packet(
    payload: object,
    *,
    receive_monotonic_ns: int,
    generation: int,
) -> RawFrame:
    """Parse one newline-delimited OpenXR JSON packet into ``RawFrame``."""

    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ControllerProtocolError("controller packet is not UTF-8") from exc
    if not isinstance(payload, str):
        raise ControllerProtocolError("controller packet must be text")
    try:
        data = json.loads(payload, object_pairs_hook=_duplicate_rejecting_object)
    except ControllerProtocolError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise ControllerProtocolError("controller packet is not valid JSON") from exc

    root = _mapping(data, "packet")
    _exact_fields(
        root,
        (
            "schema",
            "session_id",
            "frame_seq",
            "sample_time_ns",
            "reference_space",
            "hands",
        ),
        "packet",
    )
    if root.get("schema") != OPENXR_CONTROLLER_SCHEMA:
        raise ControllerProtocolError(
            f"schema must be {OPENXR_CONTROLLER_SCHEMA!r}"
        )
    session_id = root.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ControllerProtocolError("session_id must be a non-empty string")
    frame_seq = _integer(root.get("frame_seq"), "frame_seq")
    sample_time_ns = _integer(root.get("sample_time_ns"), "sample_time_ns")
    receive_ns = _integer(receive_monotonic_ns, "receive_monotonic_ns")
    local_generation = _integer(generation, "generation")
    reference_space = root.get("reference_space")
    if reference_space not in ("view", "local"):
        raise ControllerProtocolError("reference_space must be 'view' or 'local'")

    hands = _mapping(root.get("hands"), "hands")
    if set(hands) != set(SIDES):
        raise ControllerProtocolError("hands must contain exactly left and right")

    pose_data: Dict[str, np.ndarray] = {}
    button_data: Dict[str, object] = {}
    for side, short in (("left", "l"), ("right", "r")):
        hand = _mapping(hands[side], f"hands.{side}")
        _exact_fields(
            hand,
            (
                "pose_source",
                "active",
                "position_valid",
                "orientation_valid",
                "position_tracked",
                "orientation_tracked",
                "pose",
                "trigger",
                "grip",
                "joystick",
                "buttons",
            ),
            f"hands.{side}",
        )
        source = hand.get("pose_source")
        if source != "tracked_remote":
            raise ControllerProtocolError(
                f"hands.{side}.pose_source must be 'tracked_remote'"
            )
        active = _boolean(hand.get("active"), f"hands.{side}.active")
        position_valid = _boolean(
            hand.get("position_valid"), f"hands.{side}.position_valid"
        )
        orientation_valid = _boolean(
            hand.get("orientation_valid"), f"hands.{side}.orientation_valid"
        )
        position_tracked = _boolean(
            hand.get("position_tracked"), f"hands.{side}.position_tracked"
        )
        orientation_tracked = _boolean(
            hand.get("orientation_tracked"),
            f"hands.{side}.orientation_tracked",
        )
        pose = _pose(hand.get("pose"), f"hands.{side}.pose")
        if active and position_valid and orientation_valid:
            if pose is None:
                raise ControllerProtocolError(
                    f"hands.{side}.pose is required while the pose is valid"
                )
            pose_data[short] = pose
        elif pose is not None:
            raise ControllerProtocolError(
                f"hands.{side}.pose must be null while the pose is inactive or invalid"
            )

        trigger = _number(
            hand.get("trigger"),
            f"hands.{side}.trigger",
            minimum=0.0,
            maximum=1.0,
        )
        grip = _number(
            hand.get("grip"),
            f"hands.{side}.grip",
            minimum=0.0,
            maximum=1.0,
        )
        joystick = _joystick(hand.get("joystick"), f"hands.{side}.joystick")
        buttons = _button_tokens(
            side,
            _mapping(hand.get("buttons", {}), f"hands.{side}.buttons"),
        )

        prefix = side
        button_data[f"{prefix}Trig"] = [trigger]
        button_data[f"{prefix}Grip"] = [grip]
        button_data[f"{prefix}JS"] = joystick
        button_data[f"{prefix}PoseSource"] = [1.0]
        button_data[f"{prefix}ControllerActive"] = [1.0 if active else 0.0]
        button_data[f"{prefix}PositionValid"] = [1.0 if position_valid else 0.0]
        button_data[f"{prefix}OrientationValid"] = [
            1.0 if orientation_valid else 0.0
        ]
        button_data[f"{prefix}PositionTracked"] = [
            1.0 if position_tracked else 0.0
        ]
        button_data[f"{prefix}OrientationTracked"] = [
            1.0 if orientation_tracked else 0.0
        ]
        button_data.update(buttons)

    return RawFrame(
        pose_data=pose_data,
        button_data=button_data,
        provider="openxr_tcp",
        provider_session_id=session_id,
        source_frame_seq=frame_seq,
        source_sample_time_ns=sample_time_ns,
        receive_monotonic_ns=receive_ns,
        generation=local_generation,
        reference_space=reference_space,
    )
