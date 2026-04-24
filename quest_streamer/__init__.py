"""quest_streamer
==================

Stream pose + button data from a Meta Quest / Oculus controller into Python.

Extracted and decoupled from the `rwVR` research codebase.

Typical usage:

    from quest_streamer import QuestStreamer, DeltaPoseTracker

    streamer = QuestStreamer()
    while True:
        frame = streamer.read_hand("r")
        if frame is None:
            continue
        print(frame.pose, frame.trigger, frame.grip)
"""

from quest_streamer.reader import QuestStreamer, HandFrame, RawFrame
from quest_streamer.frames import X_WorldQuest, X_QuestWorld
from quest_streamer.delta_tracker import DeltaPoseTracker, TrackerStep
from quest_streamer.wrapper import QuestTeleop, TeleopSnapshot, HandState
from quest_streamer.hand_tracking import (
    HandTracker,
    HandTrackingSnapshot,
    TrackedHand,
    TrackedHead,
    X_WorldUnity,
    X_UnityWorld,
)
from quest_streamer.camera import CameraStreamer, CameraFrame, CameraSnapshot
from quest_streamer.apriltag_fusion import (
    AprilTagLocalizer,
    CameraIntrinsics,
    LocalizerSnapshot,
    TagDetection,
    TagWorldPose,
    QUEST_3S_INTRINSICS,
    QUEST_3S_INTRINSICS_LEFT,
    QUEST_3S_INTRINSICS_RIGHT,
)
from quest_streamer.utils import precise_wait

__all__ = [
    # Controller-based (oculus_reader) API
    "QuestStreamer",
    "HandFrame",
    "RawFrame",
    "DeltaPoseTracker",
    "TrackerStep",
    "QuestTeleop",
    "TeleopSnapshot",
    "HandState",
    "X_WorldQuest",
    "X_QuestWorld",
    # Hand-tracking (hand-tracking-sdk) API
    "HandTracker",
    "HandTrackingSnapshot",
    "TrackedHand",
    "TrackedHead",
    "X_WorldUnity",
    "X_UnityWorld",
    # Passthrough-camera API
    "CameraStreamer",
    "CameraFrame",
    "CameraSnapshot",
    # AprilTag fusion
    "AprilTagLocalizer",
    "CameraIntrinsics",
    "LocalizerSnapshot",
    "TagDetection",
    "TagWorldPose",
    "QUEST_3S_INTRINSICS",
    "QUEST_3S_INTRINSICS_LEFT",
    "QUEST_3S_INTRINSICS_RIGHT",
    # Shared helpers
    "precise_wait",
]

__version__ = "0.1.0"
