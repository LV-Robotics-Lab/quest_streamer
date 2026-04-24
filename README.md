# quest_streamer

`quest_streamer` is a small Python package that streams pose and button data
from a Meta Quest / Oculus controller, so the data can be used as an input
source for teleoperation, data collection, debugging, or visualization.

## What you get

- `QuestStreamer` - a thin wrapper around
  [`oculus_reader`](https://github.com/rail-berkeley/oculus_reader) that
  exposes both raw frames and a cleaner per-hand view.
- `HandFrame` / `RawFrame` - dataclasses that hold the pose (4x4), trigger,
  grip, joystick, and button state for one frame.
- `DeltaPoseTracker` - the trigger-engaged delta-pose state machine used in
  `rwVR`, decoupled from any robot so the reference frame can be anything
  (simulated EE, camera, `viser` frame, ...).
- `X_WorldQuest` / `X_QuestWorld` - the fixed transform that maps between
  the Quest's native frame and a conventional Z-up world frame (numerically
  identical to what `rwVR` used).
- `precise_wait` - the `time.monotonic`-based scheduler helper carried over
  from `rwVR` so loop timing stays consistent.

## Layout

```text
quest_streamer/
├── quest_streamer/
│   ├── __init__.py
│   ├── reader.py           # QuestStreamer, RawFrame, HandFrame
│   ├── delta_tracker.py    # DeltaPoseTracker, TrackerStep
│   ├── frames.py           # X_WorldQuest / X_QuestWorld
│   └── utils.py            # precise_wait
├── examples/
│   ├── print_raw_data.py   # connectivity sanity check
│   ├── per_hand_stream.py  # cleaned up per-hand view
│   ├── track_delta_pose.py # trigger-engaged delta pose demo
│   └── visualize_viser.py  # browser-based visualization
├── setup.py
└── README.md
```

## Installation

```bash
cd quest_streamer
pip install -e .
```

The package itself only depends on `numpy` and `scipy`. You also need to
install the Quest bridge:

```bash
# Oculus / Quest reader - same package rwVR uses
pip install git+https://github.com/rail-berkeley/oculus_reader
```

`oculus_reader` in turn requires an Android device running the companion app
(plus `adb`); see its own README for the device-side setup.

For `examples/visualize_viser.py`:

```bash
pip install viser
```

## Quick start

```python
from quest_streamer import QuestStreamer

with QuestStreamer() as streamer:
    while True:
        hand = streamer.read_hand("r")      # or "l"
        if hand is None:
            continue                         # headset not producing frames yet
        print(hand.pose)                    # 4x4 np.ndarray
        print(hand.trigger, hand.grip)       # floats in [0, 1]
        print(hand.joystick)                 # (x, y) in [-1, 1]
        print(hand.buttons)                  # {"primary": bool, "secondary": bool, ...}
```

### Trigger-engaged delta pose (teleop primitive)

```python
import numpy as np
from quest_streamer import DeltaPoseTracker, QuestStreamer

X_WorldEE = np.eye(4)

with QuestStreamer() as streamer:
    tracker = DeltaPoseTracker(streamer, which_hand="r")
    while True:
        step = tracker.step(X_WorldRef_current=X_WorldEE)
        if step is None:
            continue                         # trigger released / no data
        X_WorldEE = step.X_WorldRef_next     # feed this to your robot / sim
        gripper = step.hand.grip             # route however you like
```

This is equivalent to the core loop inside rwVR's
`SingleArmQuestAgent.act()`, but the "reference pose" is whatever you pass in
- a real robot EE, a simulated one, a `viser` frame, anything.

### World / Quest frame convention

Out of the box, `QuestStreamer.read_hand(..., in_world_frame=False)` returns
poses exactly as `OculusReader` produces them (the Quest's native frame).
Pass `in_world_frame=True` to get Z-up poses, applying the same conversion
`rwVR` uses:

```python
from quest_streamer import X_QuestWorld, X_WorldQuest
# X_world = X_QuestWorld @ X_quest @ X_WorldQuest
```

## Mapping back to rwVR

| `quest_streamer` symbol                         | rwVR location                                                    |
|-------------------------------------------------|------------------------------------------------------------------|
| `QuestStreamer.read_hand()`                     | inline `OculusReader().get_transformations_and_buttons()` calls  |
| `DeltaPoseTracker.step()`                       | `SingleArmQuestAgent.act()` in `rel/teleop/quest_to_arm.py`      |
| `X_WorldQuest` / `X_QuestWorld`                 | top of `rel/teleop/quest_to_arm.py`                              |
| `precise_wait`                                  | `rel/utils/teleop_utils.py`                                      |

## Not included on purpose

The rwVR repo also contained robot-specific glue (`XArmQuestAgent`,
`teleop_data_collection.py`, point-cloud capture, calibration). Those are
outside the scope of "Quest information acquisition" and are intentionally
left in rwVR. If you want a full teleop loop, import `quest_streamer` from
your own integration script and combine it with your robot / camera stack.
