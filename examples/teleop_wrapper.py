"""End-to-end demo of the high-level `QuestTeleop` wrapper.

Two modes:

    python examples/teleop_wrapper.py polling
    python examples/teleop_wrapper.py callback

Both spin up a background thread at 60 Hz, wait for the headset to start
producing data, then print state for ~15 seconds.
"""

import argparse
import time

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import HandState, QuestTeleop, TeleopSnapshot


def fmt_hand(h: HandState) -> str:
    if not h.connected:
        return f"[{h.which_hand}] disconnected"

    live_pos = h.pose_world[:3, 3] if h.pose_world is not None else np.zeros(3)
    live_rpy = (
        R.from_matrix(h.pose_world[:3, :3]).as_euler("xyz", degrees=True)
        if h.pose_world is not None
        else np.zeros(3)
    )
    tracked_pos = h.engaged_pose[:3, 3] if h.engaged_pose is not None else np.zeros(3)

    tags = []
    if h.just_engaged:
        tags.append("ENGAGE")
    if h.just_released:
        tags.append("RELEASE")
    if h.engaged:
        tags.append("engaged")
    tag_str = f" [{' '.join(tags)}]" if tags else ""

    return (
        f"[{h.which_hand}] "
        f"live_pos={live_pos} rpy={live_rpy} | "
        f"tracked={tracked_pos} | "
        f"trig={h.trigger:.2f} grip={h.grip:.2f}"
        f"{tag_str}"
    )


def run_polling(duration: float) -> None:
    np.set_printoptions(precision=3, suppress=True)
    with QuestTeleop(frequency=60.0) as teleop:
        print("waiting for headset ready...")
        if not teleop.wait_for_ready(timeout=10.0):
            print("timed out waiting for Quest data.")
            return
        print("ready. polling at 10 Hz for", duration, "seconds.")
        t_end = time.monotonic() + duration
        while time.monotonic() < t_end:
            snap = teleop.snapshot()
            print(f"tick={snap.tick:05d} fps={snap.fps:5.1f}")
            print("  " + fmt_hand(snap.l))
            print("  " + fmt_hand(snap.r))
            time.sleep(0.1)


def run_callback(duration: float) -> None:
    np.set_printoptions(precision=3, suppress=True)

    with QuestTeleop(frequency=60.0) as teleop:

        @teleop.on_update
        def _cb(snap: TeleopSnapshot) -> None:
            for hand in (snap.l, snap.r):
                if hand.just_engaged:
                    print(f"  ==> {hand.which_hand.upper()} ENGAGE   "
                          f"pos={hand.engaged_pose[:3, 3]}")
                if hand.just_released:
                    print(f"  ==> {hand.which_hand.upper()} RELEASE  "
                          f"pos={hand.engaged_pose[:3, 3]}")

        print("waiting for headset ready...")
        if not teleop.wait_for_ready(timeout=10.0):
            print("timed out waiting for Quest data.")
            return
        print("ready. listening for trigger events for", duration, "seconds.")
        time.sleep(duration)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["polling", "callback"])
    parser.add_argument("--duration", type=float, default=15.0)
    args = parser.parse_args()

    if args.mode == "polling":
        run_polling(args.duration)
    else:
        run_callback(args.duration)


if __name__ == "__main__":
    main()
