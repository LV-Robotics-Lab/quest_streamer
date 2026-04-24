"""ROS 2 publisher for the passthrough camera stream.

Publishes (per eye, side in {l, r}):

    /quest/camera/<side>/image_raw             sensor_msgs/Image          (BGR8)
    /quest/camera/<side>/image_raw/compressed  sensor_msgs/CompressedImage (JPEG)
    /quest/camera/<side>/camera_info           sensor_msgs/CameraInfo     (intrinsics-free stub)

Prereqs:
    source /opt/ros/jazzy/setup.bash
    source .venv-ros2/bin/activate
    adb forward tcp:9100 tcp:9100

Run:
    python examples/ros2_camera_publisher.py
    # then, in another terminal:
    ros2 topic list
    ros2 run rqt_image_view rqt_image_view /quest/camera/l/image_raw
"""

from __future__ import annotations

import argparse
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo, CompressedImage, Image

from quest_streamer import CameraSnapshot, CameraStreamer, CameraFrame


def _to_image_msg(cf: CameraFrame, frame_id: str, stamp) -> Image:
    msg = Image()
    msg.header.stamp = stamp
    msg.header.frame_id = frame_id
    msg.height = cf.height
    msg.width = cf.width
    msg.encoding = "bgr8"
    msg.is_bigendian = 0
    msg.step = cf.width * 3
    msg.data = cf.frame.tobytes()
    return msg


def _to_compressed_msg(cf: CameraFrame, frame_id: str, stamp) -> CompressedImage:
    msg = CompressedImage()
    msg.header.stamp = stamp
    msg.header.frame_id = frame_id
    msg.format = "jpeg"
    msg.data = cf.jpeg_bytes or b""
    return msg


def _to_camera_info(cf: CameraFrame, frame_id: str, stamp) -> CameraInfo:
    """Minimal CameraInfo. Intrinsics / distortion are unknown until calibration."""
    msg = CameraInfo()
    msg.header.stamp = stamp
    msg.header.frame_id = frame_id
    msg.height = cf.height
    msg.width = cf.width
    msg.distortion_model = "plumb_bob"
    msg.d = [0.0] * 5
    msg.k = [0.0] * 9
    msg.r = [0.0] * 9
    msg.p = [0.0] * 12
    return msg


class QuestCameraPublisher(Node):
    def __init__(self, cam: CameraStreamer, publish_raw: bool, publish_info: bool,
                 rate_hz: float) -> None:
        super().__init__("quest_camera_publisher")
        self.cam = cam
        self.publish_raw = publish_raw
        self.publish_info = publish_info

        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.pubs_raw = {
            "l": self.create_publisher(Image, "/quest/camera/l/image_raw", qos),
            "r": self.create_publisher(Image, "/quest/camera/r/image_raw", qos),
        }
        self.pubs_compressed = {
            "l": self.create_publisher(CompressedImage, "/quest/camera/l/image_raw/compressed", qos),
            "r": self.create_publisher(CompressedImage, "/quest/camera/r/image_raw/compressed", qos),
        }
        self.pubs_info = {
            "l": self.create_publisher(CameraInfo, "/quest/camera/l/camera_info", qos),
            "r": self.create_publisher(CameraInfo, "/quest/camera/r/camera_info", qos),
        }

        self._last_seq = {"l": -1, "r": -1}
        self._log_every = max(1, int(rate_hz))
        self._ticks = 0
        self.timer = self.create_timer(1.0 / max(rate_hz, 1.0), self._on_tick)
        self.get_logger().info(
            f"publishing {'raw+' if publish_raw else ''}compressed"
            f"{'+info' if publish_info else ''} at {rate_hz} Hz (new frames only)"
        )

    def _on_tick(self) -> None:
        snap: CameraSnapshot = self.cam.snapshot()
        now = self.get_clock().now().to_msg()
        any_published = False
        for side, cf in (("l", snap.l), ("r", snap.r)):
            if not cf.connected or cf.frame is None:
                continue
            if cf.sequence_id == self._last_seq[side]:
                continue  # no new frame
            self._last_seq[side] = cf.sequence_id
            frame_id = f"quest_camera_{side}"

            if self.publish_raw:
                self.pubs_raw[side].publish(_to_image_msg(cf, frame_id, now))
            self.pubs_compressed[side].publish(_to_compressed_msg(cf, frame_id, now))
            if self.publish_info:
                self.pubs_info[side].publish(_to_camera_info(cf, frame_id, now))
            any_published = True

        self._ticks += 1
        if any_published and self._ticks % self._log_every == 0:
            self.get_logger().info(
                f"fps={snap.fps:.1f} l.seq={snap.l.sequence_id} r.seq={snap.r.sequence_id}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--rate-hz", type=float, default=30.0,
                        help="Publish tick rate. Only new frames get published.")
    parser.add_argument("--no-raw", action="store_true",
                        help="Don't publish decoded sensor_msgs/Image (saves ~7 MB/s/eye).")
    parser.add_argument("--no-info", action="store_true",
                        help="Don't publish CameraInfo (stub anyway).")
    args = parser.parse_args()

    # Decode only if we're actually publishing raw images; saves a lot of CPU
    # when only compressed is needed.
    cam = CameraStreamer(
        host=args.host,
        port=args.port,
        decode=not args.no_raw,
    )
    try:
        print("waiting for first camera frame...", file=sys.stderr)
        if not cam.wait_for_ready(timeout=15.0):
            print("no frames in 15s; bridge will still start and publish nothing until a frame arrives.",
                  file=sys.stderr)

        rclpy.init()
        node = QuestCameraPublisher(
            cam,
            publish_raw=not args.no_raw,
            publish_info=not args.no_info,
            rate_hz=args.rate_hz,
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
        cam.stop()


if __name__ == "__main__":
    main()
