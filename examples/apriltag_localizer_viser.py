"""Viser visualization of AprilTag-based localization.

Opens a browser scene showing:

    * the world axes
    * every configured AprilTag as a flat square with its world pose
    * the current camera position / orientation (if a detection is recent)
    * the derived head pose
    * a fading trail of recent camera positions

Run:

    uv sync --extra viser
    adb forward tcp:9100 tcp:9100
    # headset: launch quest_camera_streamer, tap Start streaming
    uv run python examples/apriltag_localizer_viser.py \
        --tag-id 7 --tag-size 0.165
"""

from __future__ import annotations

import argparse
import time
from collections import deque

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import (
    AprilTagLocalizer,
    CameraStreamer,
    QUEST_3S_INTRINSICS,
    TagWorldPose,
)


def _xyzw_to_wxyz(xyzw: np.ndarray) -> np.ndarray:
    return np.asarray([xyzw[3], xyzw[0], xyzw[1], xyzw[2]], dtype=np.float64)


def _pose_to_wxyz_pos(X: np.ndarray):
    pos = X[:3, 3]
    xyzw = R.from_matrix(X[:3, :3]).as_quat()
    return pos, _xyzw_to_wxyz(xyzw)


def _build_T(xyz, rpy_deg) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R.from_euler("xyz", rpy_deg, degrees=True).as_matrix()
    T[:3, 3] = xyz
    return T


def main() -> None:
    import viser

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--eye", choices=["l", "r"], default="l")
    parser.add_argument("--tag-id", type=int, required=True)
    parser.add_argument("--tag-size", type=float, required=True)
    parser.add_argument("--tag-xyz", type=float, nargs=3, default=(0.0, 0.0, 0.0))
    parser.add_argument("--tag-rpy-deg", type=float, nargs=3, default=(0.0, 0.0, 0.0))
    parser.add_argument("--render-hz", type=float, default=30.0)
    parser.add_argument("--trail-len", type=int, default=120)
    args = parser.parse_args()

    server = viser.ViserServer()
    server.scene.add_frame("world", axes_length=0.2, axes_radius=0.005)
    server.scene.add_grid(name="floor", width=3.0, height=3.0, plane="xy")

    gui_fps = server.gui.add_markdown("**FPS**: —")
    gui_status = server.gui.add_markdown("**status**: waiting...")
    gui_pose = server.gui.add_markdown("")

    # Draw each configured tag as a small square on the scene once.
    tag_world = _build_T(args.tag_xyz, args.tag_rpy_deg)
    tags = {args.tag_id: TagWorldPose(T_world_tag=tag_world, size_m=args.tag_size)}
    for tag_id, twp in tags.items():
        pos, wxyz = _pose_to_wxyz_pos(twp.T_world_tag)
        server.scene.add_frame(f"tag/{tag_id}", position=pos, wxyz=wxyz,
                               axes_length=0.08, axes_radius=0.004)
        # square outline as 4 points → mesh
        half = twp.size_m / 2
        # tag local plane is z=0; corners in local frame
        corners_local = np.array([
            [-half, -half, 0], [+half, -half, 0],
            [+half, +half, 0], [-half, +half, 0],
        ])
        corners_world = (twp.T_world_tag[:3, :3] @ corners_local.T).T + twp.T_world_tag[:3, 3]
        server.scene.add_spline_catmull_rom(
            f"tag/{tag_id}/outline",
            positions=np.vstack([corners_world, corners_world[0:1]]),
            color=(250, 220, 50),
            line_width=3.0,
            closed=False,
        )

    trail = deque(maxlen=args.trail_len)

    cam = CameraStreamer(host=args.host, port=args.port)
    try:
        cam.wait_for_ready(timeout=15.0)
        loc = AprilTagLocalizer(
            camera=cam, tag_world_poses=tags,
            intrinsics=QUEST_3S_INTRINSICS[args.eye], eye=args.eye,
        )
        try:
            print(f"viser server up. waiting for first tag detection of id {args.tag_id}...")
            if not loc.wait_for_ready(timeout=30.0):
                print("no detection in 30s.")
            dt = 1.0 / max(args.render_hz, 1.0)

            while True:
                t_start = time.monotonic()
                snap = loc.snapshot()
                gui_fps.content = (f"**FPS**: {snap.fps:.1f}  "
                                   f"**detections**: {snap.detections_total}  "
                                   f"**age**: {snap.last_detection_age:.2f}s")

                if snap.camera_pose_world is None:
                    gui_status.content = "**status**: waiting for detection"
                else:
                    age = snap.last_detection_age
                    fresh = age < 0.5
                    gui_status.content = (
                        f"**status**: {'✅ fresh' if fresh else '⚠️ stale'}"
                        f" (last detection {age:.2f}s ago)"
                    )
                    cpos, cwxyz = _pose_to_wxyz_pos(snap.camera_pose_world)
                    hpos, hwxyz = _pose_to_wxyz_pos(snap.head_pose_world)
                    server.scene.add_frame("cam_world", position=cpos, wxyz=cwxyz,
                                           axes_length=0.08, axes_radius=0.004)
                    server.scene.add_frame("head_world", position=hpos, wxyz=hwxyz,
                                           axes_length=0.12, axes_radius=0.006)
                    server.scene.add_icosphere(
                        "cam_world/marker", radius=0.02,
                        color=(50, 200, 100) if fresh else (200, 150, 50),
                        position=tuple(cpos),
                    )

                    trail.append(cpos.copy())
                    if len(trail) >= 2:
                        server.scene.add_spline_catmull_rom(
                            "cam_world/trail",
                            positions=np.asarray(trail),
                            color=(80, 180, 240),
                            line_width=2.0,
                            closed=False,
                        )

                    crpy = R.from_matrix(
                        snap.camera_pose_world[:3, :3]
                    ).as_euler("xyz", degrees=True)
                    gui_pose.content = (
                        f"**cam pos** `[{cpos[0]:+.3f}, {cpos[1]:+.3f}, {cpos[2]:+.3f}]` m\n\n"
                        f"**cam rpy** `[{crpy[0]:+6.1f}, {crpy[1]:+6.1f}, {crpy[2]:+6.1f}]`°\n\n"
                        f"**head pos** `[{hpos[0]:+.3f}, {hpos[1]:+.3f}, {hpos[2]:+.3f}]` m"
                    )

                elapsed = time.monotonic() - t_start
                if elapsed < dt:
                    time.sleep(dt - elapsed)
        except KeyboardInterrupt:
            pass
        finally:
            loc.stop()
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()


if __name__ == "__main__":
    main()
