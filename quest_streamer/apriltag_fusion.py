"""Tag-based absolute localization from passthrough camera frames.

This module builds on `CameraStreamer` and adds drift-free world-frame pose
by running an AprilTag detector on the live passthrough stream. When a tag
with a known world pose is visible, we recover the camera's world-frame
pose geometrically (no VIO, no drift). When no tag is visible, the latest
pose is returned with a "stale since" timestamp so callers can decide how
to handle gaps (hold, extrapolate, reject).

What this *does not* do (yet): blend with VIO between tag sightings. That
would require the APK to also stream head pose per frame, which the current
`quest_camera_streamer` app (a plain 2D Android activity, not a VR app)
cannot access through Android SensorManager alone. For now, accept that:

  - pose is absolute and accurate while a tag is in view
  - pose freezes at the last detected pose when no tag is in view
  - gaps are reported as `last_detection_age` in the snapshot

Typical usage:

    from quest_streamer import CameraStreamer, AprilTagLocalizer
    from quest_streamer.apriltag_fusion import TagWorldPose, QUEST_3S_INTRINSICS

    tags = {
        7: TagWorldPose(
            # tag center at (1.0, 0.0, 1.2) in your world frame, facing +x
            T_world_tag=...,
            size_m=0.165,
        ),
    }

    with CameraStreamer() as cam:
        with AprilTagLocalizer(
            camera=cam,
            tag_world_poses=tags,
            intrinsics=QUEST_3S_INTRINSICS,
        ) as loc:
            loc.wait_for_ready(timeout=15.0)
            while True:
                snap = loc.snapshot()
                if snap.camera_pose_world is not None:
                    print(snap.camera_pose_world[:3, 3])
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer.camera import CameraFrame, CameraSnapshot, CameraStreamer


# -------------------------------------------------------------------- calibration

@dataclass(frozen=True)
class CameraIntrinsics:
    """Pinhole camera intrinsics plus the static head->camera extrinsic.

    Image dimensions reference the **streamed** frame, not the sensor active
    array. For Quest 3S streaming 1280x960 cropped from a 1280x1280 active
    array, `cy` below is pre-compensated for the 160-row top crop.
    """
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    # Extrinsic from head frame to camera frame (T_head_cam as 4x4 SE(3))
    T_head_cam: np.ndarray

    @property
    def K(self) -> np.ndarray:
        return np.array([
            [self.fx, 0.0, self.cx],
            [0.0, self.fy, self.cy],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)

    @property
    def dist(self) -> np.ndarray:
        # Meta's passthrough stream is pre-undistorted; no coefficients needed.
        return np.zeros(5, dtype=np.float64)


def _pose_from_quest_extrinsic(
    translation: Tuple[float, float, float],
    quat_xyzw: Tuple[float, float, float, float],
) -> np.ndarray:
    """Build a 4x4 SE(3) from Android `LENS_POSE_TRANSLATION` / `LENS_POSE_ROTATION`."""
    T = np.eye(4)
    T[:3, :3] = R.from_quat(np.asarray(quat_xyzw, dtype=np.float64)).as_matrix()
    T[:3, 3] = np.asarray(translation, dtype=np.float64)
    return T


# Quest 3S values dumped from `CameraCharacteristics` via logcat. Left eye.
# Intrinsic is reported against the 1280x1280 active array; our ImageReader
# crops to 1280x960 (top/bottom 160 rows removed), so we subtract 160 from
# cy.
QUEST_3S_INTRINSICS_LEFT = CameraIntrinsics(
    fx=865.5078,
    fy=865.5078,
    cx=639.85144,
    cy=641.9739 - 160.0,   # crop compensation
    width=1280,
    height=960,
    T_head_cam=_pose_from_quest_extrinsic(
        translation=(-0.03211516, -0.012897672, -0.0740648),
        quat_xyzw=(-0.9985061, -0.003634827, -0.0021247282, 0.054477543),
    ),
)

QUEST_3S_INTRINSICS_RIGHT = CameraIntrinsics(
    fx=863.8405,
    fy=863.8405,
    cx=640.6109,
    cy=640.3325 - 160.0,
    width=1280,
    height=960,
    T_head_cam=_pose_from_quest_extrinsic(
        translation=(0.03099476, -0.012974879, -0.07354871),
        quat_xyzw=(-0.99847823, 0.0015781104, 0.0056472127, 0.054834973),
    ),
)

# Default to left eye; caller can switch per-instance.
QUEST_3S_INTRINSICS: Dict[str, CameraIntrinsics] = {
    "l": QUEST_3S_INTRINSICS_LEFT,
    "r": QUEST_3S_INTRINSICS_RIGHT,
}


# -------------------------------------------------------------------- tag config

@dataclass(frozen=True)
class TagWorldPose:
    """Known placement of an AprilTag in the world frame.

    `T_world_tag` is a 4x4 SE(3) giving the tag's center and orientation in
    world coordinates. The tag's local frame convention follows
    `pupil-apriltags`: tag +x is right in the image, +y is down, +z points
    into the tag (away from the camera when the tag faces you).

    `size_m` is the physical side length of the *black* outer square.
    """
    T_world_tag: np.ndarray
    size_m: float


# ------------------------------------------------------------------ dataclasses

@dataclass
class TagDetection:
    """One detected tag from a camera frame."""
    tag_id: int
    T_cam_tag: np.ndarray          # 4x4 SE(3) in camera frame
    corners_px: np.ndarray         # (4, 2) pixel corners
    center_px: Tuple[float, float]
    decision_margin: float         # confidence score from detector
    recv_ts: float


@dataclass
class LocalizerSnapshot:
    """Latest localization output.

    `camera_pose_world` is the camera's 4x4 SE(3) in world frame, recovered
    from the most recent successful detection. `head_pose_world` is the
    derived headset pose via the static head-camera extrinsic.

    `last_detection_age` is `time.monotonic() - last_detection.recv_ts` or
    `inf` if no detection has ever happened. Callers should reject poses
    older than their app's tolerance (e.g. > 0.5 s).
    """
    camera_pose_world: Optional[np.ndarray] = None
    head_pose_world: Optional[np.ndarray] = None
    last_detection: Optional[TagDetection] = None
    last_detection_age: float = float("inf")
    detections_total: int = 0
    tick: int = 0
    fps: float = 0.0
    timestamp: float = 0.0


StateCallback = Callable[[LocalizerSnapshot], None]


# --------------------------------------------------------------- main class

class AprilTagLocalizer:
    """Continuous tag-based localization from a `CameraStreamer`.

    The detector runs in its own thread, consuming every new frame on the
    configured eye ('l' or 'r'). Detections with a matching `tag_id` in
    `tag_world_poses` produce a camera pose via the standard
    `T_world_cam = T_world_tag @ inv(T_cam_tag)` identity. With multiple tags
    visible we use the highest-confidence detection.
    """

    def __init__(
        self,
        camera: CameraStreamer,
        tag_world_poses: Dict[int, TagWorldPose],
        intrinsics: Optional[CameraIntrinsics] = None,
        eye: str = "l",
        families: str = "tag36h11",
        detection_every: int = 1,
        on_update: Optional[StateCallback] = None,
        start_now: bool = True,
    ) -> None:
        if eye not in ("l", "r"):
            raise ValueError(f"eye must be 'l' or 'r', got {eye!r}")
        if intrinsics is None:
            intrinsics = QUEST_3S_INTRINSICS[eye]
        if intrinsics.width == 0 or intrinsics.height == 0:
            raise ValueError("intrinsics width/height must be nonzero")

        try:
            from pupil_apriltags import Detector  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "AprilTagLocalizer requires pupil-apriltags. "
                "Install with `uv pip install pupil-apriltags`."
            ) from e

        self._camera = camera
        self._tag_world_poses = dict(tag_world_poses)
        self._intrinsics = intrinsics
        self._eye = eye
        self._families = families
        self._detection_every = max(1, int(detection_every))

        self._lock = threading.Lock()
        self._latest = LocalizerSnapshot()
        self._fps_window: List[float] = []
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._callbacks: List[StateCallback] = []
        if on_update is not None:
            self._callbacks.append(on_update)
        self._last_error: Optional[BaseException] = None

        if start_now:
            self.start()

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="AprilTagLocalizer", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def __enter__(self) -> "AprilTagLocalizer":
        if self._thread is None or not self._thread.is_alive():
            self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Block until the first successful detection."""
        return self._ready_event.wait(timeout=timeout)

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    # ------------------------------------------------------- subscription

    def on_update(self, callback: StateCallback) -> StateCallback:
        with self._lock:
            self._callbacks.append(callback)
        return callback

    def remove_callback(self, callback: StateCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # ------------------------------------------------------------ polling

    def snapshot(self) -> LocalizerSnapshot:
        with self._lock:
            now = time.monotonic()
            cutoff = now - 1.0
            while self._fps_window and self._fps_window[0] < cutoff:
                self._fps_window.pop(0)
            fps = float(len(self._fps_window))
            s = self._latest
            age = (
                float("inf") if s.last_detection is None
                else now - s.last_detection.recv_ts
            )
            return LocalizerSnapshot(
                camera_pose_world=None if s.camera_pose_world is None
                else s.camera_pose_world.copy(),
                head_pose_world=None if s.head_pose_world is None
                else s.head_pose_world.copy(),
                last_detection=s.last_detection,
                last_detection_age=age,
                detections_total=s.detections_total,
                tick=s.tick,
                fps=fps,
                timestamp=now,
            )

    # ------------------------------------------------------------ the loop

    def _run_loop(self) -> None:
        from pupil_apriltags import Detector
        detector = Detector(families=self._families)
        last_seq = -1
        frame_counter = 0

        try:
            while not self._stop_event.is_set():
                cam_snap = self._camera.snapshot()
                cf: CameraFrame = cam_snap.l if self._eye == "l" else cam_snap.r
                if not cf.connected or cf.frame is None or cf.sequence_id == last_seq:
                    time.sleep(0.003)
                    continue
                last_seq = cf.sequence_id
                frame_counter += 1
                if frame_counter % self._detection_every != 0:
                    continue

                try:
                    self._process_frame(detector, cf)
                except Exception as e:
                    self._last_error = e
                    traceback.print_exc()
        except Exception as e:
            self._last_error = e
            traceback.print_exc()

    def _process_frame(self, detector, cf: CameraFrame) -> None:
        import cv2
        # pupil-apriltags wants single-channel 8-bit.
        gray = cv2.cvtColor(cf.frame, cv2.COLOR_BGR2GRAY)
        K = self._intrinsics
        detections = detector.detect(
            gray,
            estimate_tag_pose=True,
            camera_params=(K.fx, K.fy, K.cx, K.cy),
            tag_size=None,  # we'll set per-tag below
        )

        # pupil-apriltags' pose_R/pose_t are populated only when tag_size is
        # given at detect() time with a single scalar. To support
        # per-tag sizes we re-solve pose via solvePnP with the known size.
        now = time.monotonic()
        best: Optional[TagDetection] = None

        for det in detections:
            known = self._tag_world_poses.get(int(det.tag_id))
            if known is None:
                continue
            T_cam_tag = self._solve_tag_pose(det.corners, known.size_m)
            if T_cam_tag is None:
                continue

            decision_margin = float(det.decision_margin)
            td = TagDetection(
                tag_id=int(det.tag_id),
                T_cam_tag=T_cam_tag,
                corners_px=np.asarray(det.corners, dtype=np.float64),
                center_px=tuple(map(float, det.center)),
                decision_margin=decision_margin,
                recv_ts=now,
            )
            if best is None or td.decision_margin > best.decision_margin:
                best = td

        if best is None:
            return

        # T_world_cam = T_world_tag @ T_tag_cam = T_world_tag @ inv(T_cam_tag)
        known = self._tag_world_poses[best.tag_id]
        T_world_cam = known.T_world_tag @ np.linalg.inv(best.T_cam_tag)
        T_world_head = T_world_cam @ np.linalg.inv(self._intrinsics.T_head_cam)

        with self._lock:
            self._latest = LocalizerSnapshot(
                camera_pose_world=T_world_cam,
                head_pose_world=T_world_head,
                last_detection=best,
                last_detection_age=0.0,
                detections_total=self._latest.detections_total + 1,
                tick=self._latest.tick + 1,
                fps=0.0,  # filled in snapshot()
                timestamp=now,
            )
            self._fps_window.append(now)
            if not self._ready_event.is_set():
                self._ready_event.set()
            callbacks = list(self._callbacks)

        snap = self.snapshot()
        for cb in callbacks:
            try:
                cb(snap)
            except Exception:
                traceback.print_exc()

    def _solve_tag_pose(self, corners: np.ndarray, size_m: float) -> Optional[np.ndarray]:
        """Recover T_cam_tag from the 4 tag corners via OpenCV solvePnP.

        Corner ordering per `pupil-apriltags`: (bottom-left, bottom-right,
        top-right, top-left) in image pixels. Object coordinates follow the
        tag frame (x right, y up, z out), so the corners sit on the z=0
        plane at (+/- size/2, +/- size/2, 0).
        """
        import cv2

        half = float(size_m) / 2.0
        obj_pts = np.array([
            [-half, -half, 0.0],
            [+half, -half, 0.0],
            [+half, +half, 0.0],
            [-half, +half, 0.0],
        ], dtype=np.float64)
        img_pts = np.asarray(corners, dtype=np.float64)

        ok, rvec, tvec = cv2.solvePnP(
            obj_pts,
            img_pts,
            self._intrinsics.K,
            self._intrinsics.dist,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
        if not ok:
            return None
        T = np.eye(4)
        T[:3, :3] = cv2.Rodrigues(rvec)[0]
        T[:3, 3] = tvec.ravel()
        return T
