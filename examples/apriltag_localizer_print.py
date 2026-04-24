"""Text readout of AprilTag-based camera localization.

Run the camera streamer on the headset, place at least one configured
AprilTag in view, and this prints the recovered camera pose in the world
frame ~1x/s.

Quick start (Quest 3S, left eye, tag 7 at world origin, 16.5 cm side):

    adb forward tcp:9100 tcp:9100
    # on headset: launch quest_camera_streamer, tap Start streaming
    uv run python examples/apriltag_localizer_print.py --tag-id 7 --tag-size 0.165

By default the tag is placed at the world origin, facing +x. Override with
`--tag-xyz --tag-rpy-deg` to define a different world placement.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import (
    AprilTagLocalizer,
    CameraStreamer,
    QUEST_3S_INTRINSICS,
    TagWorldPose,
)


def _build_T(xyz, rpy_deg) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R.from_euler("xyz", rpy_deg, degrees=True).as_matrix()
    T[:3, 3] = xyz
    return T


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--eye", choices=["l", "r"], default="l")
    parser.add_argument("--tag-id", type=int, required=True)
    parser.add_argument("--tag-size", type=float, required=True,
                        help="tag side length in meters (outer black square)")
    parser.add_argument("--tag-xyz", type=float, nargs=3, default=(0.0, 0.0, 0.0),
                        help="tag center position in world frame (m)")
    parser.add_argument("--tag-rpy-deg", type=float, nargs=3, default=(0.0, 0.0, 0.0),
                        help="tag RPY orientation in world frame (deg)")
    parser.add_argument("--duration", type=float, default=30.0)
    args = parser.parse_args()

    np.set_printoptions(precision=3, suppress=True)

    tag_world = _build_T(args.tag_xyz, args.tag_rpy_deg)
    tags = {args.tag_id: TagWorldPose(T_world_tag=tag_world, size_m=args.tag_size)}

    with CameraStreamer(host=args.host, port=args.port) as cam:
        cam.wait_for_ready(timeout=10.0)
        with AprilTagLocalizer(
            camera=cam,
            tag_world_poses=tags,
            intrinsics=QUEST_3S_INTRINSICS[args.eye],
            eye=args.eye,
        ) as loc:
            print(f"waiting for first detection of tag {args.tag_id}...")
            if not loc.wait_for_ready(timeout=args.duration):
                print("no detection. Is the tag in view of the chosen eye?")
                return
            print("got it. printing pose every second.")
            t_end = time.monotonic() + args.duration
            while time.monotonic() < t_end:
                snap = loc.snapshot()
                if snap.camera_pose_world is None:
                    print("no valid detection yet.")
                else:
                    cpos = snap.camera_pose_world[:3, 3]
                    crpy = R.from_matrix(
                        snap.camera_pose_world[:3, :3]
                    ).as_euler("xyz", degrees=True)
                    hpos = snap.head_pose_world[:3, 3]
                    print(
                        f"fps={snap.fps:4.1f}  detections={snap.detections_total}  "
                        f"age={snap.last_detection_age:5.2f}s  "
                        f"cam=[{cpos[0]:+.3f}, {cpos[1]:+.3f}, {cpos[2]:+.3f}] m  "
                        f"rpy=[{crpy[0]:+5.1f}, {crpy[1]:+5.1f}, {crpy[2]:+5.1f}]°  "
                        f"head=[{hpos[0]:+.3f}, {hpos[1]:+.3f}, {hpos[2]:+.3f}] m"
                    )
                time.sleep(1.0)


if __name__ == "__main__":
    main()
