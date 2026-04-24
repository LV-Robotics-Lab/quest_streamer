"""ROS 2 TF broadcaster driven by `QuestTeleop`.

Publishes four transforms from the fixed frame `world`:

    world -> quest_l            (live left controller, pose_world)
    world -> quest_r            (live right controller, pose_world)
    world -> quest_l_engaged    (left  engaged_pose, moves only while L trigger held)
    world -> quest_r_engaged    (right engaged_pose, moves only while R trigger held)

Prerequisites:
    1. ROS 2 Jazzy installed + sourced (`source /opt/ros/jazzy/setup.bash`).
    2. A venv that can import rclpy AND quest_streamer / oculus_reader, e.g.:
           python3.12 -m venv .venv-ros2 --system-site-packages
           source .venv-ros2/bin/activate
           pip install -e . -e ~/third_party/oculus_reader

Run:
    source /opt/ros/jazzy/setup.bash
    source .venv-ros2/bin/activate
    python examples/ros2_tf_broadcaster.py                     # USB
    python examples/ros2_tf_broadcaster.py --ip 10.254.108.157 # WiFi

In another terminal visualize with:
    source /opt/ros/jazzy/setup.bash
    rviz2 -d examples/quest_viz.rviz      # (config optional; see TF display below)
"""

from __future__ import annotations

import argparse
import math
import sys

import numpy as np
import rclpy
from geometry_msgs.msg import TransformStamped
from rclpy.node import Node
import tf2_ros
from tf_transformations import quaternion_from_matrix

from quest_streamer import HandState, QuestTeleop, TeleopSnapshot


def _to_transform(X: np.ndarray, parent: str, child: str, stamp) -> TransformStamped:
    t = TransformStamped()
    t.header.stamp = stamp
    t.header.frame_id = parent
    t.child_frame_id = child

    pos = X[:3, 3]
    t.transform.translation.x = float(pos[0])
    t.transform.translation.y = float(pos[1])
    t.transform.translation.z = float(pos[2])

    # tf_transformations.quaternion_from_matrix expects a 4x4 and returns xyzw
    q = quaternion_from_matrix(X)
    t.transform.rotation.x = float(q[0])
    t.transform.rotation.y = float(q[1])
    t.transform.rotation.z = float(q[2])
    t.transform.rotation.w = float(q[3])
    return t


class QuestTfBroadcaster(Node):
    def __init__(self, teleop: QuestTeleop, publish_hz: float,
                 world_frame: str = "world",
                 publish_engaged: bool = True) -> None:
        super().__init__("quest_tf_broadcaster")
        self.teleop = teleop
        self.world_frame = world_frame
        self.publish_engaged = publish_engaged
        self.br = tf2_ros.TransformBroadcaster(self)

        self._log_every = max(1, int(publish_hz))  # log roughly once per second
        self._tick_count = 0

        period = 1.0 / max(publish_hz, 1.0)
        self.timer = self.create_timer(period, self._on_tick)
        self.get_logger().info(
            f"streaming TFs at {publish_hz:.1f} Hz; "
            f"engaged_pose frames: {'on' if publish_engaged else 'off'}"
        )

    def _on_tick(self) -> None:
        snap: TeleopSnapshot = self.teleop.snapshot()
        now = self.get_clock().now().to_msg()

        tfs = []
        for hand in (snap.l, snap.r):
            if not hand.connected or hand.pose_world is None:
                continue
            tfs.append(_to_transform(
                hand.pose_world, self.world_frame, f"quest_{hand.which_hand}", now,
            ))
            if self.publish_engaged and hand.engaged_pose is not None:
                tfs.append(_to_transform(
                    hand.engaged_pose, self.world_frame,
                    f"quest_{hand.which_hand}_engaged", now,
                ))

        if tfs:
            self.br.sendTransform(tfs)

        self._tick_count += 1
        if self._tick_count % self._log_every == 0:
            self.get_logger().info(
                f"tick={snap.tick} fps={snap.fps:.1f} "
                f"l.connected={snap.l.connected} "
                f"r.connected={snap.r.connected} "
                f"l.engaged={snap.l.engaged} r.engaged={snap.r.engaged}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default=None,
                        help="Quest IP for WiFi mode; omit for USB.")
    parser.add_argument("--port", type=int, default=5555,
                        help="adb network port (default 5555).")
    parser.add_argument("--frequency", type=float, default=60.0,
                        help="QuestTeleop polling frequency (Hz).")
    parser.add_argument("--publish-hz", type=float, default=60.0,
                        help="TF publish rate (Hz).")
    parser.add_argument("--no-engaged", action="store_true",
                        help="Don't publish the engaged_pose frames.")
    parser.add_argument("--world-frame", default="world",
                        help="Parent frame name to publish under.")
    args = parser.parse_args()

    teleop = QuestTeleop(
        frequency=args.frequency,
        ip_address=args.ip,
        port=args.port,
    )
    try:
        print("waiting for headset ready...", file=sys.stderr)
        if not teleop.wait_for_ready(timeout=15.0):
            print("no Quest data within 15s; is the headset on a head?", file=sys.stderr)
            # keep running — rclpy will show "no data" via connected=False

        rclpy.init()
        node = QuestTfBroadcaster(
            teleop,
            publish_hz=args.publish_hz,
            world_frame=args.world_frame,
            publish_engaged=not args.no_engaged,
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
        teleop.stop()


if __name__ == "__main__":
    main()
