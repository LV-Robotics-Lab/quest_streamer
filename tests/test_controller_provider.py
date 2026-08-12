from __future__ import annotations

import json

import pytest

from quest_streamer import (
    ControllerProtocolError,
    OpenXRSocketControllerProvider,
    hand_frame_from_raw,
    parse_openxr_controller_packet,
)

IDENTITY = [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
]


def packet(*, seq: int = 1, session: str = "session-a") -> str:
    hand = {
        "pose_source": "tracked_remote",
        "active": True,
        "position_valid": True,
        "orientation_valid": True,
        "position_tracked": True,
        "orientation_tracked": True,
        "pose": IDENTITY,
        "trigger": 0.75,
        "grip": 0.25,
        "joystick": [0.1, -0.2],
        "buttons": {
            "primary": True,
            "secondary": False,
            "thumb_rest": True,
            "stick": False,
        },
    }
    return json.dumps(
        {
            "schema": "nero.quest_controller.raw.v1",
            "session_id": session,
            "frame_seq": seq,
            "sample_time_ns": 123_000_000 + seq,
            "reference_space": "local",
            "hands": {"left": hand, "right": hand},
        },
        separators=(",", ":"),
    )


def test_strict_packet_maps_to_public_hand_surface() -> None:
    frame = parse_openxr_controller_packet(
        packet(), receive_monotonic_ns=9_000_000_000, generation=4
    )
    left = hand_frame_from_raw(frame, "l")

    assert left is not None
    assert frame.provider == "openxr_tcp"
    assert frame.provider_session_id == "session-a"
    assert frame.source_frame_seq == 1
    assert frame.reference_space == "local"
    assert left.pose_source == "tracked_remote"
    assert left.position_valid is True
    assert left.orientation_tracked is True
    assert left.trigger == pytest.approx(0.75)
    assert left.buttons["primary"] is True


def test_duplicate_and_unknown_fields_are_rejected() -> None:
    duplicate = packet().replace('"frame_seq":1', '"frame_seq":1,"frame_seq":2')
    with pytest.raises(ControllerProtocolError, match="duplicate JSON field"):
        parse_openxr_controller_packet(
            duplicate, receive_monotonic_ns=1, generation=1
        )

    unknown = json.loads(packet())
    unknown["unexpected"] = True
    with pytest.raises(ControllerProtocolError, match="unknown=unexpected"):
        parse_openxr_controller_packet(
            json.dumps(unknown), receive_monotonic_ns=1, generation=1
        )


def test_inactive_or_invalid_hand_cannot_carry_a_pose() -> None:
    data = json.loads(packet())
    data["hands"]["left"]["active"] = False
    with pytest.raises(ControllerProtocolError, match="pose must be null"):
        parse_openxr_controller_packet(
            json.dumps(data), receive_monotonic_ns=1, generation=1
        )

    data["hands"]["left"]["pose"] = None
    frame = parse_openxr_controller_packet(
        json.dumps(data), receive_monotonic_ns=2, generation=2
    )
    assert "l" not in frame.pose_data
    assert frame.button_data["leftControllerActive"] == [0.0]


def test_provider_rejects_non_monotonic_sequence_within_session() -> None:
    provider = OpenXRSocketControllerProvider(start_now=False)
    provider._accept_line(packet(seq=2).encode())

    with pytest.raises(ControllerProtocolError, match="increase strictly"):
        provider._accept_line(packet(seq=2).encode())


def test_provider_allows_sequence_reset_for_new_session() -> None:
    provider = OpenXRSocketControllerProvider(start_now=False)
    provider._accept_line(packet(seq=9, session="old").encode())
    provider._accept_line(packet(seq=0, session="new").encode())

    frame = provider.read()
    assert frame is not None
    assert frame.provider_session_id == "new"
    assert frame.source_frame_seq == 0


def test_provider_returns_latest_frame_and_counts_skips() -> None:
    provider = OpenXRSocketControllerProvider(start_now=False)
    for seq in (1, 2, 3):
        provider._accept_line(packet(seq=seq).encode())

    frame = provider.read()
    assert frame is not None
    assert frame.source_frame_seq == 3
    assert provider.dropped_frames == 2
    assert provider.read() is None
