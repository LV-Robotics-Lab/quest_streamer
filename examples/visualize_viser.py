"""Visualize a Quest controller's pose in a `viser` browser viewer.

Left controller shows up as a frame named "quest/l", right as "quest/r". When
a trigger is pressed, an additional yellow frame "quest/<h>/engaged" tracks
the delta-engaged reference pose.

    pip install viser
    python examples/visualize_viser.py
"""

import time

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import DeltaPoseTracker, QuestStreamer, precise_wait


def _xyzw_to_wxyz(xyzw: np.ndarray) -> np.ndarray:
    return xyzw[[3, 0, 1, 2]]


def _add_pose_frame(server, name: str, X: np.ndarray, axes_length: float = 0.1) -> None:
    pos = X[:3, 3]
    wxyz = _xyzw_to_wxyz(R.from_matrix(X[:3, :3]).as_quat())
    server.scene.add_frame(name, position=pos, wxyz=wxyz, axes_length=axes_length)


def main() -> None:
    import viser  # imported lazily so the package itself has no hard dep

    server = viser.ViserServer()
    print("Open the URL printed above to view the Quest pose.")

    frequency = 30.0
    dt = 1.0 / frequency

    X_WorldRef_l = np.eye(4)
    X_WorldRef_r = np.eye(4)
    X_WorldRef_r[:3, 3] = [0.3, 0.0, 0.0]

    with QuestStreamer() as streamer:
        tracker_l = DeltaPoseTracker(streamer, which_hand="l")
        tracker_r = DeltaPoseTracker(streamer, which_hand="r")

        t_start = time.monotonic()
        frame_idx = 0
        while True:
            t_cycle_end = t_start + (frame_idx + 1) * dt

            for label, tracker, X_WorldRef in (("l", tracker_l, X_WorldRef_l),
                                               ("r", tracker_r, X_WorldRef_r)):
                hand = streamer.read_hand(label, in_world_frame=True)
                if hand is not None:
                    _add_pose_frame(server, f"quest/{label}", hand.pose, axes_length=0.08)

                step = tracker.step(X_WorldRef_current=X_WorldRef)
                if step is not None and not step.just_released:
                    if label == "l":
                        X_WorldRef_l = step.X_WorldRef_next
                        _add_pose_frame(server, "quest/l/engaged", X_WorldRef_l, 0.12)
                    else:
                        X_WorldRef_r = step.X_WorldRef_next
                        _add_pose_frame(server, "quest/r/engaged", X_WorldRef_r, 0.12)

            precise_wait(t_cycle_end)
            frame_idx += 1


if __name__ == "__main__":
    main()
