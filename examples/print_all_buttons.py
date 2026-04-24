"""Print everything the wrapper exposes per hand, once per second.

Useful as a reference when you're not sure whether a physical button is
surfaced or not.

    python examples/print_all_buttons.py
"""

import time

import numpy as np

from quest_streamer import QuestTeleop


def main() -> None:
    np.set_printoptions(precision=3, suppress=True)
    with QuestTeleop(frequency=60.0) as teleop:
        if not teleop.wait_for_ready(timeout=10.0):
            print("timed out waiting for Quest data.")
            return
        for _ in range(20):
            snap = teleop.snapshot()
            print(f"--- tick={snap.tick}  fps={snap.fps:.1f} ---")
            for hand in (snap.l, snap.r):
                pressed = [k for k, v in hand.buttons.items() if v]
                print(
                    f"[{hand.which_hand}] "
                    f"pos={hand.pose_world[:3, 3] if hand.pose_world is not None else None}  "
                    f"trig={hand.trigger:.2f} grip={hand.grip:.2f}  "
                    f"js=({hand.joystick[0]:+.2f}, {hand.joystick[1]:+.2f})  "
                    f"buttons_pressed={pressed}"
                )
            time.sleep(1.0)


if __name__ == "__main__":
    main()
