"""Connectivity test for the hand-tracking pipeline.

    # Wired over USB:
    adb reverse tcp:8000 tcp:8000
    uv run python examples/hand_tracking_print.py

    # Wireless UDP:
    uv run python examples/hand_tracking_print.py --transport udp --port 9000

    # Wireless TCP server-on-PC (the APK connects out to this machine):
    uv run python examples/hand_tracking_print.py --transport tcp_client \
        --host <PC IP> --port 9000

Prints a one-liner per hand per ~0.5s showing wrist position (world FLU,
meters), wrist orientation (roll/pitch/yaw degrees), and index fingertip
position.
"""

import argparse
import time

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import HandTracker, TrackedHand


def fmt_hand(h: TrackedHand) -> str:
    if not h.connected or h.wrist_world is None:
        return f"[{h.side}] waiting..."
    pos = h.wrist_world[:3, 3]
    rpy = R.from_matrix(h.wrist_world[:3, :3]).as_euler("xyz", degrees=True)
    tip = h.landmarks_world[8]  # IndexTip index in STREAMED_JOINT_NAMES
    return (
        f"[{h.side}] wrist pos={pos}  rpy={rpy}  "
        f"index_tip={tip}  seq={h.sequence_id}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["tcp_server", "tcp_client", "udp"],
                        default="tcp_server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--duration", type=float, default=30.0)
    args = parser.parse_args()

    np.set_printoptions(precision=3, suppress=True)

    with HandTracker(
        transport=args.transport,
        host=args.host,
        port=args.port,
    ) as ht:
        print(
            f"waiting for first frame ({args.transport} {args.host}:{args.port})..."
        )
        if not ht.wait_for_ready(timeout=20.0):
            print("no data within 20s. Is the APK running? Does adb reverse / UDP host match?")
            return
        print("streaming.")

        t_end = time.monotonic() + args.duration
        while time.monotonic() < t_end:
            snap = ht.snapshot()
            print(f"tick={snap.tick:05d} fps={snap.fps:5.1f}")
            print("  " + fmt_hand(snap.l))
            print("  " + fmt_hand(snap.r))
            time.sleep(0.5)


if __name__ == "__main__":
    main()
