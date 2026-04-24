"""Stream a single hand's pose, trigger, grip, and joystick.

    python examples/per_hand_stream.py --hand r --world-frame

`--world-frame` converts the pose from the Quest's native frame into a Z-up
"world" frame, matching what rwVR uses when driving a robot arm.
"""

import argparse
import time

import numpy as np

from quest_streamer import QuestStreamer, precise_wait


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hand", choices=["l", "r"], default="r")
    parser.add_argument("--frequency", type=float, default=20.0)
    parser.add_argument("--world-frame", action="store_true",
                        help="Report pose in the Z-up world frame.")
    args = parser.parse_args()

    dt = 1.0 / args.frequency
    np.set_printoptions(precision=3, suppress=True)

    with QuestStreamer() as streamer:
        t_start = time.monotonic()
        frame_idx = 0
        while True:
            t_cycle_end = t_start + (frame_idx + 1) * dt

            hand = streamer.read_hand(args.hand, in_world_frame=args.world_frame)
            if hand is None:
                print("no data, quest not yet ready")
            else:
                print(
                    f"[{hand.which_hand}] "
                    f"pos={hand.pose[:3, 3]} "
                    f"trig={hand.trigger:.2f} grip={hand.grip:.2f} "
                    f"js=({hand.joystick[0]:+.2f}, {hand.joystick[1]:+.2f}) "
                    f"buttons={hand.buttons}"
                )

            precise_wait(t_cycle_end)
            frame_idx += 1


if __name__ == "__main__":
    main()
