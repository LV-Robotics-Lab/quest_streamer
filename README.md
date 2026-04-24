# quest_streamer

`quest_streamer` is a Python package that streams both **controller** data
(pose + buttons) and **bare-hand** data (21 finger joint positions per hand)
from a Meta Quest headset, via two complementary Android-side APKs.

## Three modes

| Mode | What it gives you | Android side | PC-side Python |
|---|---|---|---|
| **Controller** | 6-DoF pose of each Touch controller, trigger/grip/joystick, 6 discrete buttons per hand | `rail-berkeley/oculus_reader` APK | `oculus_reader` (ADB logcat) |
| **Hand-tracking** | 6-DoF wrist pose + 21 finger-joint positions per bare hand | `wengmister/hand-tracking-streamer` APK | `hand-tracking-sdk` (TCP/UDP socket) |
| **Passthrough camera** | MJPEG stream from both forward RGB passthrough cameras (1280×960 per eye, ~37 Hz combined) | `android/quest_camera_streamer/` (in-repo, native Kotlin) | `CameraStreamer` (TCP socket) |

Each mode is independent. On Quest, only one VR application runs at a time,
so the controller / hand-tracking / camera APKs are typically used one at a
time. The PC-side wrappers can coexist freely in the same Python process.

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

### Passthrough-camera mode

- **`CameraStreamer` — high-level wrapper.** Consumes the MJPEG TCP stream
  from `quest_camera_streamer` (a small in-repo Kotlin app under
  `android/quest_camera_streamer/`). Exposes the same `snapshot()` /
  `on_update()` / `wait_for_ready()` surface, with per-eye
  `CameraFrame` objects carrying decoded BGR `np.ndarray` + raw JPEG bytes.
  Wired for `adb forward tcp:9100 tcp:9100` over USB by default.

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
├── assets/
│   ├── oculus_teleop.apk            # controller-side companion app (vendored)
│   └── hand_tracking_streamer.apk   # hand-tracking companion app (vendored)
├── android/
│   └── quest_camera_streamer/       # Kotlin source for the passthrough-camera APK
│                                    # Build with ./gradlew assembleDebug
├── quest_streamer/
│   ├── __init__.py
│   ├── reader.py                    # QuestStreamer, RawFrame, HandFrame
│   ├── delta_tracker.py             # DeltaPoseTracker, TrackerStep
│   ├── wrapper.py                   # QuestTeleop, TeleopSnapshot, HandState
│   ├── hand_tracking.py             # HandTracker, HandTrackingSnapshot, TrackedHand, TrackedHead
│   ├── camera.py                    # CameraStreamer, CameraFrame, CameraSnapshot
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
│   # -- passthrough camera mode --
│   ├── camera_preview.py            # OpenCV window showing L+R live preview
│   ├── ros2_camera_publisher.py     # sensor_msgs/Image + CompressedImage publisher
│   │
│   # -- hand-tracking mode --
│   ├── hand_tracking_print.py       # text printout per hand per ~0.5s
│   ├── hand_tracking_viser.py       # viser skeleton viz
│   ├── ros2_hand_tracking_broadcaster.py  # ROS 2 TF + MarkerArray publisher
│   └── quest_hand_tracking.rviz     # rviz2 config for hand-tracking
└── scripts/
    ├── bootstrap_oculus_reader.sh      # clones pinned oculus_reader, adb-installs assets/oculus_teleop.apk
    └── bootstrap_hand_tracking.sh      # pip-installs hand-tracking-sdk, adb-installs assets/hand_tracking_streamer.apk
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

`oculus_reader` is not on PyPI, so a bootstrap script clones a pinned commit
of the upstream repo and pip-installs it into the active venv. The
companion APK is vendored in `assets/oculus_teleop.apk` so no LFS fetch is
needed at install time:

```bash
bash scripts/bootstrap_oculus_reader.sh
```

Overrides:

- `QUEST_STREAMER_THIRD_PARTY=/your/path` — where to clone the repo (default
  `~/third_party`).
- `OCULUS_READER_REV=<sha>` — pin a different upstream commit, or `HEAD` to
  track upstream.
- `SKIP_APK=1` — install only the Python package.

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

### 5. Push the companion APK to the headset

The bootstrap script already does this; to re-install manually:

```bash
adb install -r -g assets/oculus_teleop.apk
```

The Quest now runs `com.rail.oculus.teleop` whenever it is awake.

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

This `pip install`s `hand-tracking-sdk` (from PyPI) into the active venv
and `adb install`s the vendored APK (`assets/hand_tracking_streamer.apk`)
onto the connected Quest. Set `SKIP_APK=1` to install the SDK only, or
override `HAND_TRACKING_SDK_VERSION` to pin a different SDK release.

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

## Installation — passthrough camera mode

The camera streamer is an in-repo Kotlin app. Build + install require a JDK
and the Android SDK command-line tools; **no Android Studio**.

### 1. One-time toolchain

```bash
sudo apt install -y openjdk-17-jdk

# Android SDK cmdline-tools (~150 MB download; ~1 GB after sdkmanager install)
mkdir -p ~/Android/cmdline-tools
cd /tmp && curl -fLsS -o cmdtools.zip \
    https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip -q cmdtools.zip
mv cmdline-tools ~/Android/cmdline-tools/latest

export ANDROID_HOME=$HOME/Android
export PATH="$HOME/Android/cmdline-tools/latest/bin:$PATH"
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
```

### 2. Build + install the APK

```bash
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export ANDROID_HOME=$HOME/Android
cd android/quest_camera_streamer
echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew assembleDebug
adb install -r -g app/build/outputs/apk/debug/app-debug.apk
```

Produces `com.oculus.camerademo` on the headset (same applicationId as the
streamer APK; it displaces the Meta sample). The Kotlin source is under
`android/quest_camera_streamer/app/src/main/java/com/oculus/camerademo/`.

### 3. Wire + test

```bash
adb forward tcp:9100 tcp:9100
uv pip install opencv-python    # one-time
uv run python examples/camera_preview.py
```

On the headset, launch `quest_camera_streamer`, tap **Start streaming**.
Both 1280×960 eye previews appear in-app and on the PC-side OpenCV window.
About 37 FPS combined over USB (~18 FPS per eye).

Gotchas, all hit during testing:

- **HMD dismount pauses the camera.** Horizon OS revokes camera access when
  the headset is off the user's head; you'll see `Camera 50 error: disabled`
  in logcat. The TCP server keeps listening but no new frames arrive, so
  `wait_for_ready` blocks. Put the headset back on and press **Stop → Start**
  in the app to reopen the cameras.
- **Use `adb forward`, not `adb reverse`.** The APK is the TCP *server*; PC
  is the client. (`adb reverse` is what the hand-tracking pipeline uses,
  because there the APK is the client.)
- **Restart streaming after reinstalling the APK.** Newer APK → new process
  → old bound socket lingers in TIME_WAIT for a few seconds; first Start
  after install may EADDRINUSE. Second Start works.

### 4. ROS 2 publishing (optional)

Prerequisite: install `opencv-python` and ensure scipy/numpy are compatible
in the ROS 2 venv (the default `scipy` from Noble's `--system-site-packages`
is bound to numpy<2; pull newer ones into the venv):

```bash
source /opt/ros/jazzy/setup.bash
source .venv-ros2/bin/activate
pip install -U "scipy>=1.13" "numpy<3" opencv-python
```

Run the publisher:

```bash
# Terminal 1
adb forward tcp:9100 tcp:9100   # (if not already set)
python examples/ros2_camera_publisher.py

# Terminal 2
source /opt/ros/jazzy/setup.bash
ros2 topic list | grep quest
# /quest/camera/l/camera_info
# /quest/camera/l/image_raw
# /quest/camera/l/image_raw/compressed
# /quest/camera/r/camera_info
# /quest/camera/r/image_raw
# /quest/camera/r/image_raw/compressed

ros2 topic hz /quest/camera/l/image_raw      # ~17 Hz, matching publisher
```

Visualize one eye at a time with `rqt_image_view` (Python 3.12 ABI, so launch
through the ROS 2 venv):

```bash
# Make the venv's python3.12 win over any conda python3.13 on PATH:
export PATH="$PWD/.venv-ros2/bin:$PATH"
ros2 run rqt_image_view rqt_image_view /quest/camera/l/image_raw
```

The dropdown in the window switches between `/quest/camera/l/image_raw` and
`/quest/camera/r/image_raw`. To see both simultaneously, open two instances.

Topic rates observed end-to-end: each eye publishes at ~16–17 Hz on both the
raw and compressed topics; underlying MJPEG stream is ~18 FPS per eye. Add
`--no-raw` to the publisher if you only need `/image_raw/compressed` (saves
~7 MB/s/eye of bus traffic).

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

### Passthrough-camera quick start

```python
from quest_streamer import CameraStreamer

with CameraStreamer(host="127.0.0.1", port=9100) as cam:
    cam.wait_for_ready(timeout=10.0)

    while True:
        snap = cam.snapshot()
        if snap.l.connected:
            left_bgr = snap.l.frame             # (960, 1280, 3) uint8
            left_jpeg = snap.l.jpeg_bytes        # raw JPEG bytes
        if snap.r.connected:
            right_bgr = snap.r.frame
```

`CameraFrame` fields: `side`, `connected`, `frame` (BGR `np.ndarray`),
`jpeg_bytes` (raw), `width`, `height`, `sequence_id`, `recv_ts`.

Pass `decode=False` to skip OpenCV decoding (saves CPU when you only want
the JPEG bytes to forward somewhere else).

Wire setup on the PC:

```bash
adb forward tcp:9100 tcp:9100
```

then on the headset open the `quest_camera_streamer` app and tap **Start
streaming**.

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

### World / Quest frame convention

`QuestStreamer.read_hand(..., in_world_frame=False)` returns poses exactly as
`OculusReader` produces them (the Quest's native frame). Pass
`in_world_frame=True` (or use `HandState.pose_world` from the wrapper) to get
the Z-up version:

```python
from quest_streamer import X_QuestWorld, X_WorldQuest
X_world = X_QuestWorld @ X_quest @ X_WorldQuest
```

## Drift-free localization via AprilTags

`AprilTagLocalizer` consumes the `quest_camera_streamer` MJPEG feed, runs
an AprilTag detector on each left-eye frame, and — when a tag with a known
world pose is visible — recovers the camera's world-frame pose
geometrically. No drift: every reading is an absolute measurement against
the physical tag.

### What you need

1. **A printed AprilTag.** Default detector family is `tag36h11`. Generate
   at e.g. <https://chev.me/arucogen/> (switch to "36h11"). Print *big*
   — for 1280×960 input, a **16 cm** tag stays detectable up to ~2 m.
2. **Install the two Python deps** (already implicit in our wrappers; only
   the detector is extra):

   ```bash
   uv pip install pupil-apriltags opencv-python
   ```
3. **Run the camera APK** as usual (see "Installation — passthrough camera
   mode"):

   ```bash
   scripts/switch_mode.sh camera
   # headset: tap Start streaming in quest_camera_streamer
   ```

### Quick start

```python
import numpy as np
from quest_streamer import (
    CameraStreamer, AprilTagLocalizer, TagWorldPose, QUEST_3S_INTRINSICS,
)

# Tag placement in your world frame. Use np.eye(4) if the tag IS the origin.
T_world_tag = np.eye(4)

tags = {7: TagWorldPose(T_world_tag=T_world_tag, size_m=0.165)}

with CameraStreamer() as cam, AprilTagLocalizer(
    camera=cam,
    tag_world_poses=tags,
    intrinsics=QUEST_3S_INTRINSICS["l"],
) as loc:
    loc.wait_for_ready(timeout=15.0)
    while True:
        snap = loc.snapshot()
        if snap.camera_pose_world is not None:
            print(snap.camera_pose_world[:3, 3])     # meters, world frame
            print(snap.head_pose_world[:3, 3])        # derived via head-cam extrinsic
```

### Examples

```bash
# text readout
uv run python examples/apriltag_localizer_print.py --tag-id 7 --tag-size 0.165

# 3D viser scene with trail
uv run python examples/apriltag_localizer_viser.py --tag-id 7 --tag-size 0.165
```

### Known limits of this MVP

- **Only works while a configured tag is in view.** When the tag leaves the
  frame the last pose is held; `snap.last_detection_age` tells you how
  stale it is. For real continuous localization (tag-gaps bridged by VIO)
  we'd need the camera APK to also stream the Quest's live head pose,
  which a plain Android app doesn't have access to today.
- **Intrinsics are hardcoded for Quest 3S** from a single headset's dump
  of `CameraCharacteristics.LENS_INTRINSIC_CALIBRATION`. Numbers differ
  slightly per unit; use a proper checkerboard calibration for anything
  better than cm-level accuracy.
- **Only left eye is used by default.** `--eye r` switches to the right
  camera with its own intrinsics and extrinsic.

## Mode switcher

Quest runs exactly one VR app at a time, so our three modes are mutually
exclusive. `scripts/switch_mode.sh` stops whatever is running and launches
the chosen one, plus sets up the right `adb forward` / `adb reverse`:

```bash
scripts/switch_mode.sh controller   # rail-berkeley/oculus_reader
scripts/switch_mode.sh hands        # hand-tracking-streamer (adb reverse 8000)
scripts/switch_mode.sh camera       # quest_camera_streamer (adb forward 9100)
scripts/switch_mode.sh stop         # force-stop all + clear adb port maps
```
