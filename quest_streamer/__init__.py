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
from quest_streamer.delta_tracker import DeltaPoseTracker
from quest_streamer.utils import precise_wait

__all__ = [
    "QuestStreamer",
    "HandFrame",
    "RawFrame",
    "DeltaPoseTracker",
    "X_WorldQuest",
    "X_QuestWorld",
    "precise_wait",
]

__version__ = "0.1.0"
