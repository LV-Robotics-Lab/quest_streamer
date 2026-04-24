"""Trigger-engaged delta-pose tracker demo.

This is the teleop primitive used by `rwVR`: hold the trigger to engage control,
release it to disengage. While engaged, the controller's delta motion is added
to a reference "world" pose of your choice. Here we start the reference at the
origin and simply print its updated value.

    python examples/track_delta_pose.py --hand r
"""

import argparse
import time

import numpy as np

from quest_streamer import DeltaPoseTracker, QuestStreamer, precise_wait


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hand", choices=["l", "r"], default="r")
    parser.add_argument("--frequency", type=float, default=20.0)
    parser.add_argument("--translation-scale", type=float, default=1.0)
    args = parser.parse_args()

    dt = 1.0 / args.frequency
    np.set_printoptions(precision=3, suppress=True)

    X_WorldRef = np.eye(4)

    with QuestStreamer() as streamer:
        tracker = DeltaPoseTracker(
            streamer=streamer,
            which_hand=args.hand,
            translation_scaling_factor=args.translation_scale,
        )

        t_start = time.monotonic()
        frame_idx = 0
        while True:
            t_cycle_end = t_start + (frame_idx + 1) * dt

            step = tracker.step(X_WorldRef_current=X_WorldRef)
            if step is None:
                print("idle (trigger released / no data)")
            else:
                if step.just_engaged:
                    print("==> ENGAGE")
                elif step.just_released:
                    print("==> RELEASE")
                    X_WorldRef = step.X_WorldRef_next  # freeze last pose

                if not step.just_released:
                    X_WorldRef = step.X_WorldRef_next

                print(
                    f"pos={X_WorldRef[:3, 3]} "
                    f"grip={step.hand.grip:.2f} "
                    f"trig={step.hand.trigger:.2f}"
                )

            precise_wait(t_cycle_end)
            frame_idx += 1


if __name__ == "__main__":
    main()
