"""Print raw Quest pose + button data as it streams in.

    python examples/print_raw_data.py

Useful as a quick "does my Quest link work" smoke test.
"""

import time

import numpy as np

from quest_streamer import QuestStreamer, precise_wait


def main() -> None:
    frequency = 20.0
    dt = 1.0 / frequency

    np.set_printoptions(precision=3, suppress=True)

    with QuestStreamer() as streamer:
        t_start = time.monotonic()
        frame_idx = 0
        while True:
            t_cycle_end = t_start + (frame_idx + 1) * dt

            frame = streamer.read()
            if frame is None:
                print("no data, quest not yet ready")
            else:
                for hand in ("l", "r"):
                    pose = frame.pose_data.get(hand)
                    if pose is None:
                        continue
                    print(f"[{hand}] pos = {pose[:3, 3]}")
                print(
                    f"    leftTrig={frame.button_data.get('leftTrig')} "
                    f"rightTrig={frame.button_data.get('rightTrig')} "
                    f"leftGrip={frame.button_data.get('leftGrip')} "
                    f"rightGrip={frame.button_data.get('rightGrip')}"
                )

            precise_wait(t_cycle_end)
            frame_idx += 1


if __name__ == "__main__":
    main()
