# quest_streamer

`quest_streamer` is a Python package that streams both **controller** data
(pose + buttons) and **bare-hand** data (21 finger joint positions per hand)
from a Meta Quest headset, via two complementary Android-side APKs.

## Two modes

| Mode | What it gives you | Upstream APK | Upstream Python |
|---|---|---|---|
| **Controller** | 6-DoF pose of each Touch controller, trigger/grip/joystick, 6 discrete buttons per hand | `rail-berkeley/oculus_reader` | `oculus_reader` (ADB logcat) |
| **Hand-tracking** | 6-DoF wrist pose + 21 finger-joint positions per bare hand | `wengmister/hand-tracking-streamer` | `hand-tracking-sdk` (TCP/UDP socket) |

The two modes are fully independent and can be used side by side on the same
headset (different APKs), though only one is typically active at a time.

## What you get

### Controller mode — three API layers

- **`QuestTeleop` — high-level wrapper (recommended).** Spawns a background
  thread at a fixed rate, manages both hands with an internal
  `DeltaPoseTracker` each, self-manages a reference pose per hand, and gives
  you either thread-safe polling (`snapshot()`) or callbacks (`on_update`).
  `wait_for_ready()` blocks until the headset actually produces data.
- **`DeltaPoseTracker` — single-hand teleop primitive.** Trigger-engaged
  delta-pose state machine. Caller-pumped; reference frame can be anything.
- **`QuestStreamer` — thin reader.** Wraps
  [`oculus_reader`](https://github.com/rail-berkeley/oculus_reader). Exposes
  raw frames and a cleaner per-hand view (`HandFrame` / `RawFrame`).

### Hand-tracking mode

- **`HandTracker` — high-level wrapper.** Spawns a background thread
  consuming `hand_tracking_sdk.HTSClient`. Supports UDP, TCP-server, and
  TCP-client transport. Exposes the same `snapshot()` / `on_update()` /
  `wait_for_ready()` surface as `QuestTeleop`. Per-hand state includes the
  wrist pose and 21 joint positions, in both native Unity-LH and Z-up FLU
  world frames.

### Shared

- `X_WorldQuest` / `X_QuestWorld` — transform between the Quest's controller
  native frame and a Z-up world frame.
- `X_WorldUnity` / `X_UnityWorld` — transform between hand-tracking Unity-LH
  and Z-up FLU world frames.
- `precise_wait` — `time.monotonic`-based scheduler helper.

## Layout

```text
quest_streamer/
├── pyproject.toml                   # project + uv config
├── uv.lock                          # pinned dep graph, reproducible installs
├── quest_streamer/
│   ├── __init__.py
│   ├── reader.py                    # QuestStreamer, RawFrame, HandFrame
│   ├── delta_tracker.py             # DeltaPoseTracker, TrackerStep
│   ├── wrapper.py                   # QuestTeleop, TeleopSnapshot, HandState
│   ├── hand_tracking.py             # HandTracker, HandTrackingSnapshot, TrackedHand
│   ├── frames.py                    # X_WorldQuest / X_QuestWorld
│   └── utils.py                     # precise_wait
├── examples/
│   # -- controller mode --
│   ├── print_raw_data.py            # connectivity sanity check
│   ├── per_hand_stream.py           # cleaned up per-hand view
│   ├── track_delta_pose.py          # trigger-engaged delta pose (single-hand)
│   ├── teleop_wrapper.py            # full QuestTeleop demo: polling + callback
│   ├── print_all_buttons.py         # prints every readable field per snapshot
│   ├── visualize_wrapper_viser.py   # viser viz of QuestTeleop
│   ├── ros2_tf_broadcaster.py       # ROS 2 TF broadcaster for controllers
│   ├── quest_viz.rviz               # rviz2 config for controller TFs
│   │
│   # -- hand-tracking mode --
│   ├── hand_tracking_print.py       # text printout per hand per ~0.5s
│   ├── hand_tracking_viser.py       # viser skeleton viz
│   ├── ros2_hand_tracking_broadcaster.py  # ROS 2 TF + MarkerArray publisher
│   └── quest_hand_tracking.rviz     # rviz2 config for hand-tracking
└── scripts/
    ├── bootstrap_oculus_reader.sh      # controller side: oculus_reader + APK
    └── bootstrap_hand_tracking.sh      # hand-tracking side: SDK + APK
```

## Installation (uv-based, recommended)

The project uses [uv](https://docs.astral.sh/uv/) for environment management.
`pyproject.toml` declares the Python dependencies; `uv.lock` pins them for
reproducible installs.

### 1. Install `uv`

```bash
pip install --user uv      # or: curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Create the project venv

```bash
cd quest_streamer
uv sync                    # creates .venv and installs all deps from uv.lock
```

For the optional `viser` visualization demo, add the extra:

```bash
uv sync --extra viser
```

### 3. Install `oculus_reader` into the venv

`oculus_reader` is not on PyPI, and its GitHub repo ships the companion APK
through git-lfs — neither `pip install git+...` nor `uv add` pulls LFS blobs,
so the standard install leaves you with a 132-byte pointer file in place of
the 7.3 MB APK and `OculusReader().install()` fails.

A bootstrap script handles the dance:

```bash
bash scripts/bootstrap_oculus_reader.sh
```

It clones `rail-berkeley/oculus_reader` into `~/third_party/oculus_reader`,
downloads the real APK via GitHub's LFS media URL, and installs the package
into the active `.venv` with `uv pip install -e`. Override the checkout
location with `QUEST_STREAMER_THIRD_PARTY=/your/path` if you prefer.

### 4. Set up the Quest side

On Linux you also need:

```bash
sudo apt install -y adb
# grant your user access to the Oculus USB device (VID 2833):
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2833", MODE="0666", GROUP="plugdev"' \
    | sudo tee /etc/udev/rules.d/51-oculus.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

On the headset:

1. Enable **Developer Mode** (via the Meta Horizon mobile app, Devices →
   your Quest → Headset settings).
2. Plug in USB, put on the headset, and tap **Allow** on the USB-debugging
   prompt (check "Always allow from this computer").
3. Confirm detection: `adb devices` should show the Quest's serial as
   `device`, not `unauthorized` or `no permissions`.

### 5. Push the companion APK to the headset (once)

```bash
uv run python -c "from oculus_reader.reader import OculusReader; OculusReader(run=False).install()"
```

You should see `APK installed successfully.` The Quest now runs
`com.rail.oculus.teleop` whenever it is awake.

### 6. Test it

With the headset **worn** (or the proximity sensor covered, so the VR
runtime stays awake) and controllers powered:

```bash
uv run python examples/teleop_wrapper.py polling
uv run python examples/print_all_buttons.py
```

## Installation — hand-tracking mode

Hand-tracking is a separate pipeline that uses a **different** APK
(`wengmister/hand-tracking-streamer`) and a **different** Python SDK
(`hand-tracking-sdk`). There's no overlap with `oculus_reader`, so you can
install either or both. The hand-tracking APK streams joint data over a raw
socket rather than through `adb logcat`.

### 1. Bootstrap the SDK + APK

```bash
bash scripts/bootstrap_hand_tracking.sh
```

This `pip install`s `hand-tracking-sdk` into the active venv, clones
`wengmister/hand-tracking-streamer`, and `adb install`s the APK onto the
connected Quest. Set `SKIP_APK=1` to install the SDK only.

`hand-tracking-sdk` requires Python ≥3.12, which is why it is installed via
this bootstrap script rather than declared in `pyproject.toml` (that would
force the base project's minimum Python up for everyone).

### 2. On the headset

Open the **hand-tracking-streamer** app from the Unknown Sources library,
and configure its transport. The easiest mode for USB-connected development:

- On the headset app: select **TCP server** (the APK acts as client), host
  `127.0.0.1`, port `8000`.
- On the PC: `adb reverse tcp:8000 tcp:8000` so the APK can reach the PC
  through the USB tether.

For wireless options see the upstream CONNECTIONS.md.

### 3. Test it

```bash
uv run python examples/hand_tracking_print.py
uv run python examples/hand_tracking_viser.py        # browser viz
```

With the headset on and both hands visible in front of you, you should see
live wrist poses + 21 joint positions per hand stream in.

## Quick start

### Recommended: the high-level wrapper

```python
from quest_streamer import QuestTeleop

with QuestTeleop(frequency=60.0) as teleop:
    teleop.wait_for_ready(timeout=10.0)

    while True:
        snap = teleop.snapshot()

        # edge events are sticky-until-consumed — safe to poll slower than 60 Hz
        if snap.r.just_engaged:
            print("right trigger engaged")

        # while the right trigger is held, drive your robot off engaged_pose
        if snap.r.engaged:
            command_robot(snap.r.engaged_pose, gripper=snap.r.grip)
```

Event-driven consumption:

```python
from quest_streamer import QuestTeleop, TeleopSnapshot

with QuestTeleop(frequency=60.0) as teleop:
    @teleop.on_update
    def _(snap: TeleopSnapshot) -> None:
        if snap.r.just_engaged:
            print("engage at", snap.r.engaged_pose[:3, 3])

    teleop.wait_for_ready()
    ...  # do other work; callback fires every tick on the bg thread
```

Change the reference pose a hand is tracking (e.g. to snapshot the live
robot EE on the next engage):

```python
teleop.set_reference_pose("r", X_WorldEE)
```

Other knobs: `teleop.reset(hand=None)`, `teleop.set_translation_scaling("r", 1.5)`,
`teleop.last_error`.

### Raw, low-level reader

```python
from quest_streamer import QuestStreamer

with QuestStreamer() as streamer:
    while True:
        hand = streamer.read_hand("r")      # or "l"
        if hand is None:
            continue                         # headset not producing frames yet
        print(hand.pose, hand.trigger, hand.grip, hand.joystick, hand.buttons)
```

### Caller-pumped single-hand delta tracker

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

### Hand-tracking quick start

```python
from quest_streamer import HandTracker

with HandTracker(transport="tcp_server", host="0.0.0.0", port=8000) as ht:
    ht.wait_for_ready(timeout=15.0)

    while True:
        snap = ht.snapshot()
        if snap.r.connected:
            wrist_pos = snap.r.wrist_world[:3, 3]         # (3,) in FLU meters
            joints    = snap.r.landmarks_world             # (21, 3) in FLU
            index_tip = snap.r.landmarks_world[8]          # IndexTip
```

`snap.l` / `snap.r` are `TrackedHand` instances with:

- `wrist`, `wrist_world` — 4x4 SE(3); `wrist_world` is in the Z-up FLU frame.
- `landmarks`, `landmarks_world` — `(21, 3)` arrays in the same joint order
  as `hand_tracking_sdk.STREAMED_JOINT_NAMES` (Wrist, thumb×4, index×4,
  middle×4, ring×4, little×4).
- `connected`, `sequence_id`, `recv_ts_ns`, `source_ts_ns`,
  `source_frame_seq`, `timestamp`.

Transport options match the upstream SDK: `"tcp_server"` (PC listens; pairs
with `adb reverse tcp:8000 tcp:8000` for USB), `"tcp_client"` (PC connects
out, matches APK's TCP-client mode), `"udp"` (low-setup WiFi broadcast).

## Complete reference: what the wrapper exposes

Everything below was verified on a physical Meta Quest 3S with real Touch
controllers. `snap = teleop.snapshot()` returns a `TeleopSnapshot`; each hand
is a `HandState`. The two hands are completely symmetric.

### `TeleopSnapshot`

| Field | Type | Meaning |
|---|---|---|
| `l` | `HandState` | left controller |
| `r` | `HandState` | right controller |
| `tick` | `int` | monotonically increasing bg-loop tick counter |
| `fps` | `float` | measured bg-loop frequency over the last second |
| `timestamp` | `float` | `time.monotonic()` when the snapshot was produced |

### `HandState` — pose fields

All poses are `numpy.ndarray`, shape `(4, 4)`, `float64`, homogeneous SE(3)
matrices (rotation top-left 3x3, translation top-right 3x1 in **meters**).

| Field | Frame | Update policy |
|---|---|---|
| `pose` | Quest native (Y-up, -Z forward, X right) | every tick while `connected` |
| `pose_world` | Z-up world (`X_QuestWorld @ pose @ X_WorldQuest`) | every tick |
| `engaged_pose` | Z-up world | only while trigger is held; frozen on release |

The origin is the Quest's own tracking-space origin (fixed at boot / recenter).
To pull rotation or translation out:

```python
pose[:3, :3]          # 3x3 rotation
pose[:3, 3]           # 3-vector translation in meters

from scipy.spatial.transform import Rotation as R
quat_xyzw = R.from_matrix(pose[:3, :3]).as_quat()
euler_xyz = R.from_matrix(pose[:3, :3]).as_euler("xyz", degrees=True)
```

### `HandState` — analog inputs

| Field | Type | Range | Source |
|---|---|---|---|
| `trigger` | `float` | `[0.0, 1.0]` | index-finger trigger |
| `grip` | `float` | `[0.0, 1.0]` | hand grip |
| `joystick` | `(float, float)` | each in `[-1.0, 1.0]` | (x, y) of thumbstick |

### `HandState.buttons` — six discrete buttons per hand

All returned as `bool` inside `hand.buttons: dict[str, bool]`. Names are
hand-agnostic; left and right report the same keys but correspond to the
physical button in that hand.

| Key | Right hand | Left hand |
|---|---|---|
| `primary` | A face button | X face button |
| `secondary` | B face button | Y face button |
| `thumb_rest` | thumb touching the rest pad (capacitive) | same |
| `stick` | right joystick clicked in | left joystick clicked in |
| `grip_bool` | digital grip flag (SDK-derived) | same |
| `trigger_bool` | digital trigger flag (SDK-derived) | same |

### `HandState` — wrapper-derived state

| Field | Type | Meaning |
|---|---|---|
| `connected` | `bool` | this hand has at least one pose frame |
| `engaged` | `bool` | trigger value currently above threshold (default 0.5) |
| `just_engaged` | `bool` | rising edge — set for one bg tick and sticky until the next `snapshot()` |
| `just_released` | `bool` | falling edge — same semantics |
| `timestamp` | `float` | `time.monotonic()` when this `HandState` was sampled |

### Not available from this pipeline

The following exist on the hardware but are not forwarded by the
`com.rail.oculus.teleop` APK, so `quest_streamer` cannot surface them:

- Menu / Oculus / Home system buttons.
- Headset (HMD) pose — only controllers are tracked.
- Finger-joint / hand-tracking data.
- Haptic feedback. The pipeline is read-only; there is no way to make the
  controllers vibrate from Python without replacing the Android-side app.

### World / Quest frame convention

`QuestStreamer.read_hand(..., in_world_frame=False)` returns poses exactly as
`OculusReader` produces them (the Quest's native frame). Pass
`in_world_frame=True` (or use `HandState.pose_world` from the wrapper) to get
the Z-up version:

```python
from quest_streamer import X_QuestWorld, X_WorldQuest
X_world = X_QuestWorld @ X_quest @ X_WorldQuest
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
