"""Viser visualization of the hand-tracking `HandTracker` wrapper.

Browser scene shows, for each tracked hand:

* A coordinate frame at the wrist (`quest/<hand>/wrist`).
* 21 spheres at the joint landmarks, colored by hand.
* Line segments drawing the hand skeleton (palm + 5 fingers).

    uv sync --extra viser
    uv run python examples/hand_tracking_viser.py

Transport selection matches the SDK (`--transport tcp_server|tcp_client|udp`).
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import HandTracker, TrackedHand


# STREAMED_JOINT_NAMES order (from hand_tracking_sdk.constants):
#   0: Wrist
#   1-4:  Thumb   (Metacarpal, Proximal, Distal, Tip)
#   5-8:  Index   (Proximal, Intermediate, Distal, Tip)
#   9-12: Middle  (Proximal, Intermediate, Distal, Tip)
#  13-16: Ring    (Proximal, Intermediate, Distal, Tip)
#  17-20: Little  (Proximal, Intermediate, Distal, Tip)
#
# Skeleton bone list — each tuple is (joint_index_a, joint_index_b).
SKELETON_BONES = [
    # Wrist -> proximal of each finger
    (0, 1), (0, 5), (0, 9), (0, 13), (0, 17),
    # Thumb
    (1, 2), (2, 3), (3, 4),
    # Index
    (5, 6), (6, 7), (7, 8),
    # Middle
    (9, 10), (10, 11), (11, 12),
    # Ring
    (13, 14), (14, 15), (15, 16),
    # Little
    (17, 18), (18, 19), (19, 20),
]


def _xyzw_to_wxyz(xyzw: np.ndarray) -> np.ndarray:
    return np.asarray([xyzw[3], xyzw[0], xyzw[1], xyzw[2]], dtype=np.float64)


def _pose_to_wxyz_pos(X: np.ndarray):
    pos = X[:3, 3]
    xyzw = R.from_matrix(X[:3, :3]).as_quat()
    return pos, _xyzw_to_wxyz(xyzw)


def _render_hand(server, hand: TrackedHand, color) -> None:
    prefix = f"quest/{hand.side}"
    if not hand.connected or hand.landmarks_world is None or hand.wrist_world is None:
        return

    # Wrist frame
    pos, wxyz = _pose_to_wxyz_pos(hand.wrist_world)
    server.scene.add_frame(
        f"{prefix}/wrist",
        position=pos, wxyz=wxyz,
        axes_length=0.06, axes_radius=0.004,
    )

    # Joint spheres
    for i, p in enumerate(hand.landmarks_world):
        server.scene.add_icosphere(
            f"{prefix}/joint_{i:02d}",
            radius=0.010 if i == 8 or i == 4 else 0.008,  # fingertip-emphasis
            color=color,
            position=tuple(p),
        )

    # Skeleton bones
    for a, b in SKELETON_BONES:
        pa = hand.landmarks_world[a]
        pb = hand.landmarks_world[b]
        # viser has add_spline_catmull_rom or add_line_segments; simplest is
        # two icospheres plus an elongated icosahedron cylinder would be
        # overkill — use add_spline_catmull_rom with 2 control points, which
        # renders a thin tube.
        server.scene.add_spline_catmull_rom(
            f"{prefix}/bone_{a:02d}_{b:02d}",
            positions=np.asarray([pa, pb], dtype=np.float64),
            color=color,
            line_width=3.0,
        )


def main() -> None:
    import viser

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["tcp_server", "tcp_client", "udp"],
                        default="tcp_server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--render-hz", type=float, default=30.0)
    args = parser.parse_args()

    server = viser.ViserServer()
    server.scene.add_frame("world", axes_length=0.2, axes_radius=0.005)
    server.scene.add_grid(name="floor", width=3.0, height=3.0, plane="xy")

    gui_fps = server.gui.add_markdown("**FPS**: —")
    gui_l = server.gui.add_markdown("**left** — waiting")
    gui_r = server.gui.add_markdown("**right** — waiting")
    gui_head = server.gui.add_markdown("**head** — waiting")

    ht = HandTracker(
        transport=args.transport,
        host=args.host,
        port=args.port,
    )
    try:
        print("waiting for first frame...")
        ht.wait_for_ready(timeout=30.0)
        print("streaming.")
        dt = 1.0 / max(args.render_hz, 1.0)

        while True:
            t_start = time.monotonic()
            snap = ht.snapshot()
            gui_fps.content = f"**FPS**: {snap.fps:.1f}  **tick**: {snap.tick}"

            for hand, color, gui in ((snap.l, (60, 140, 240), gui_l),
                                     (snap.r, (240, 120, 50), gui_r)):
                _render_hand(server, hand, color)
                if hand.connected and hand.wrist_world is not None:
                    pos = hand.wrist_world[:3, 3]
                    gui.content = (
                        f"**{hand.side}** — connected\n\n"
                        f"- wrist pos: `[{pos[0]:+.3f}, {pos[1]:+.3f}, {pos[2]:+.3f}]` m\n"
                        f"- seq: `{hand.sequence_id}`"
                    )
                else:
                    gui.content = f"**{hand.side}** — waiting"

            # Head pose (shown as a larger frame + green sphere).
            if snap.head.connected and snap.head.pose_world is not None:
                h_pos, h_wxyz = _pose_to_wxyz_pos(snap.head.pose_world)
                server.scene.add_frame(
                    "quest/head",
                    position=h_pos, wxyz=h_wxyz,
                    axes_length=0.10, axes_radius=0.006,
                )
                server.scene.add_icosphere(
                    "quest/head/marker",
                    radius=0.06,
                    color=(100, 200, 100),
                    position=tuple(h_pos),
                    wxyz=h_wxyz,
                )
                gui_head.content = (
                    f"**head** — connected\n\n"
                    f"- pos: `[{h_pos[0]:+.3f}, {h_pos[1]:+.3f}, {h_pos[2]:+.3f}]` m\n"
                    f"- seq: `{snap.head.sequence_id}`"
                )
            else:
                gui_head.content = "**head** — not streaming (toggle Head Pose in the APK menu)"

            elapsed = time.monotonic() - t_start
            if elapsed < dt:
                time.sleep(dt - elapsed)
    except KeyboardInterrupt:
        pass
    finally:
        ht.stop()


if __name__ == "__main__":
    main()
