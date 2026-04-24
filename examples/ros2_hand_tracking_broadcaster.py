"""ROS 2 TF + marker broadcaster driven by `HandTracker`.

Publishes:

* `/tf`: `world -> hand_<l|r>_wrist` for each tracked hand (6-DoF).
* `/tf`: `world -> hand_<l|r>_joint_<NN>` for each of 21 landmarks per hand
  (positions only; orientation identity).
* `/hands/<l|r>/markers`: `visualization_msgs/MarkerArray` with spheres + a
  skeleton line strip per hand, matching the viser viz.

Prerequisites:
    source /opt/ros/jazzy/setup.bash
    source .venv-ros2/bin/activate
    pip install "hand-tracking-sdk>=1.0,<2.0"

Run:
    python examples/ros2_hand_tracking_broadcaster.py
    python examples/ros2_hand_tracking_broadcaster.py --transport udp --port 9000

Visualize:
    rviz2 -d examples/quest_hand_tracking.rviz
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import rclpy
from geometry_msgs.msg import TransformStamped
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
import tf2_ros
from tf_transformations import quaternion_from_matrix
from visualization_msgs.msg import Marker, MarkerArray

from quest_streamer import HandTracker, HandTrackingSnapshot, TrackedHand


SKELETON_BONES = [
    (0, 1), (0, 5), (0, 9), (0, 13), (0, 17),
    (1, 2), (2, 3), (3, 4),
    (5, 6), (6, 7), (7, 8),
    (9, 10), (10, 11), (11, 12),
    (13, 14), (14, 15), (15, 16),
    (17, 18), (18, 19), (19, 20),
]


def _to_transform(X: np.ndarray, parent: str, child: str, stamp) -> TransformStamped:
    t = TransformStamped()
    t.header.stamp = stamp
    t.header.frame_id = parent
    t.child_frame_id = child

    pos = X[:3, 3]
    t.transform.translation.x = float(pos[0])
    t.transform.translation.y = float(pos[1])
    t.transform.translation.z = float(pos[2])
    q = quaternion_from_matrix(X)
    t.transform.rotation.x = float(q[0])
    t.transform.rotation.y = float(q[1])
    t.transform.rotation.z = float(q[2])
    t.transform.rotation.w = float(q[3])
    return t


def _point_transform(x: float, y: float, z: float, parent: str, child: str, stamp) -> TransformStamped:
    t = TransformStamped()
    t.header.stamp = stamp
    t.header.frame_id = parent
    t.child_frame_id = child
    t.transform.translation.x = float(x)
    t.transform.translation.y = float(y)
    t.transform.translation.z = float(z)
    t.transform.rotation.w = 1.0
    return t


def _markers_for_hand(hand: TrackedHand, world_frame: str, stamp, color_rgb) -> MarkerArray:
    """Build a MarkerArray for one hand: joints (spheres) + skeleton (line strip)."""
    arr = MarkerArray()
    if not hand.connected or hand.landmarks_world is None:
        return arr

    # Joints — one sphere list
    spheres = Marker()
    spheres.header.stamp = stamp
    spheres.header.frame_id = world_frame
    spheres.ns = f"hand_{hand.side}"
    spheres.id = 0
    spheres.type = Marker.SPHERE_LIST
    spheres.action = Marker.ADD
    spheres.scale.x = 0.015
    spheres.scale.y = 0.015
    spheres.scale.z = 0.015
    spheres.color.r = color_rgb[0] / 255.0
    spheres.color.g = color_rgb[1] / 255.0
    spheres.color.b = color_rgb[2] / 255.0
    spheres.color.a = 1.0
    for p in hand.landmarks_world:
        from geometry_msgs.msg import Point
        pt = Point()
        pt.x, pt.y, pt.z = float(p[0]), float(p[1]), float(p[2])
        spheres.points.append(pt)
    arr.markers.append(spheres)

    # Skeleton — line list (pairs of points per bone)
    lines = Marker()
    lines.header.stamp = stamp
    lines.header.frame_id = world_frame
    lines.ns = f"hand_{hand.side}"
    lines.id = 1
    lines.type = Marker.LINE_LIST
    lines.action = Marker.ADD
    lines.scale.x = 0.004
    lines.color.r = color_rgb[0] / 255.0
    lines.color.g = color_rgb[1] / 255.0
    lines.color.b = color_rgb[2] / 255.0
    lines.color.a = 0.8
    from geometry_msgs.msg import Point
    for a, b in SKELETON_BONES:
        pa = hand.landmarks_world[a]
        pb = hand.landmarks_world[b]
        pta, ptb = Point(), Point()
        pta.x, pta.y, pta.z = float(pa[0]), float(pa[1]), float(pa[2])
        ptb.x, ptb.y, ptb.z = float(pb[0]), float(pb[1]), float(pb[2])
        lines.points.append(pta)
        lines.points.append(ptb)
    arr.markers.append(lines)
    return arr


class HandTrackingBroadcaster(Node):
    def __init__(
        self, tracker: HandTracker, publish_hz: float,
        world_frame: str = "world", publish_landmark_tfs: bool = True,
    ) -> None:
        super().__init__("hand_tracking_broadcaster")
        self.tracker = tracker
        self.world_frame = world_frame
        self.publish_landmark_tfs = publish_landmark_tfs

        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self.tf_br = tf2_ros.TransformBroadcaster(self)
        self.marker_pubs = {
            "l": self.create_publisher(MarkerArray, "/hands/l/markers", qos),
            "r": self.create_publisher(MarkerArray, "/hands/r/markers", qos),
        }
        self._log_every = max(1, int(publish_hz))
        self._count = 0

        period = 1.0 / max(publish_hz, 1.0)
        self.timer = self.create_timer(period, self._on_tick)
        self.get_logger().info(
            f"streaming hand-tracking TFs + markers at {publish_hz:.1f} Hz; "
            f"landmark TFs: {'on' if publish_landmark_tfs else 'off'}"
        )

    def _on_tick(self) -> None:
        snap: HandTrackingSnapshot = self.tracker.snapshot()
        now = self.get_clock().now().to_msg()

        tfs = []
        for hand, color in ((snap.l, (60, 140, 240)), (snap.r, (240, 120, 50))):
            if not hand.connected:
                continue
            if hand.wrist_world is not None:
                tfs.append(_to_transform(
                    hand.wrist_world, self.world_frame, f"hand_{hand.side}_wrist", now,
                ))
            if self.publish_landmark_tfs and hand.landmarks_world is not None:
                for i, p in enumerate(hand.landmarks_world):
                    tfs.append(_point_transform(
                        p[0], p[1], p[2],
                        self.world_frame, f"hand_{hand.side}_joint_{i:02d}", now,
                    ))
            markers = _markers_for_hand(hand, self.world_frame, now, color)
            if markers.markers:
                self.marker_pubs[hand.side].publish(markers)

        if tfs:
            self.tf_br.sendTransform(tfs)

        self._count += 1
        if self._count % self._log_every == 0:
            self.get_logger().info(
                f"tick={snap.tick} fps={snap.fps:.1f} "
                f"l.connected={snap.l.connected} r.connected={snap.r.connected}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["tcp_server", "tcp_client", "udp"],
                        default="tcp_server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--publish-hz", type=float, default=60.0)
    parser.add_argument("--no-landmark-tfs", action="store_true",
                        help="Don't publish per-joint TFs (22 per hand) to reduce /tf load.")
    parser.add_argument("--world-frame", default="world")
    args = parser.parse_args()

    tracker = HandTracker(
        transport=args.transport,
        host=args.host,
        port=args.port,
    )
    try:
        print("waiting for first hand-tracking frame...", file=sys.stderr)
        if not tracker.wait_for_ready(timeout=30.0):
            print(
                "no data in 30s; still publishing empty TFs until a frame arrives.",
                file=sys.stderr,
            )

        rclpy.init()
        node = HandTrackingBroadcaster(
            tracker,
            publish_hz=args.publish_hz,
            world_frame=args.world_frame,
            publish_landmark_tfs=not args.no_landmark_tfs,
        )
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
    finally:
        tracker.stop()


if __name__ == "__main__":
    main()
