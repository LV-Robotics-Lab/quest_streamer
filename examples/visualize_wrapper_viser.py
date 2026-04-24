"""Viser visualization of the `QuestTeleop` wrapper.

Opens a browser-based scene showing, for both hands:

* The live world-frame pose (small axes at the controller).
* A colored sphere glued to the controller (blue = left, orange = right),
  whose brightness scales with the trigger value.
* The engaged / tracker pose (large axes; only moves while the trigger is
  held, frozen on release — see `engaged_pose` in HandState).

A GUI panel on the right shows trigger / grip / joystick readouts, the
pressed discrete buttons, and the background-loop FPS.

Run:
    uv sync --extra viser                           # one time
    uv run python examples/visualize_wrapper_viser.py             # USB mode
    uv run python examples/visualize_wrapper_viser.py --ip 10.254.108.157  # WiFi

Open the printed URL in a browser. The controllers' frames only update
while the headset is worn / awake.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.spatial.transform import Rotation as R

from quest_streamer import HandState, QuestTeleop


def _xyzw_to_wxyz(xyzw: np.ndarray) -> np.ndarray:
    return np.asarray([xyzw[3], xyzw[0], xyzw[1], xyzw[2]], dtype=np.float64)


def _pose_to_wxyz_pos(X: np.ndarray):
    pos = X[:3, 3]
    xyzw = R.from_matrix(X[:3, :3]).as_quat()
    wxyz = _xyzw_to_wxyz(xyzw)
    return pos, wxyz


def _fmt_hand_readout(h: HandState) -> str:
    if not h.connected:
        return f"**{h.which_hand}** — waiting for data"
    pressed = sorted(k for k, v in h.buttons.items() if v)
    pos = h.pose_world[:3, 3] if h.pose_world is not None else np.zeros(3)
    engaged_pos = (
        h.engaged_pose[:3, 3] if h.engaged_pose is not None else np.zeros(3)
    )
    engaged_tag = "✅ engaged" if h.engaged else "released"
    return (
        f"**{h.which_hand}** — {engaged_tag}\n\n"
        f"- pose_world pos: `[{pos[0]:+.3f}, {pos[1]:+.3f}, {pos[2]:+.3f}]` m\n"
        f"- engaged pos:    `[{engaged_pos[0]:+.3f}, {engaged_pos[1]:+.3f}, {engaged_pos[2]:+.3f}]` m\n"
        f"- trigger: `{h.trigger:.2f}`  grip: `{h.grip:.2f}`\n"
        f"- joystick: `({h.joystick[0]:+.2f}, {h.joystick[1]:+.2f})`\n"
        f"- buttons pressed: `{pressed or '-'}`"
    )


def main() -> None:
    import viser

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default=None,
                        help="Quest IP address for WiFi mode (e.g. 10.254.108.157). "
                             "If omitted, uses the USB-connected device.")
    parser.add_argument("--port", type=int, default=5555,
                        help="adb network port (default 5555).")
    parser.add_argument("--frequency", type=float, default=60.0,
                        help="QuestTeleop background frequency (Hz).")
    parser.add_argument("--render-hz", type=float, default=30.0,
                        help="Viser update rate (Hz). Can be lower than --frequency.")
    args = parser.parse_args()

    server = viser.ViserServer()
    print(f"Open the URL printed above. Listening at {args.frequency:.0f} Hz, "
          f"rendering at {args.render_hz:.0f} Hz.")

    # Static scene elements
    server.scene.add_frame("world", axes_length=0.2, axes_radius=0.005)
    server.scene.add_grid(
        name="floor",
        width=3.0,
        height=3.0,
        plane="xy",
    )

    # Dynamic GUI readouts
    gui_fps = server.gui.add_markdown("**FPS**: — ")
    gui_tick = server.gui.add_markdown("**tick**: —")
    gui_mode = server.gui.add_markdown(
        f"**mode**: {'WiFi @ ' + args.ip if args.ip else 'USB'}"
    )
    gui_l = server.gui.add_markdown("**left** — waiting")
    gui_r = server.gui.add_markdown("**right** — waiting")

    teleop = QuestTeleop(
        frequency=args.frequency,
        ip_address=args.ip,
        port=args.port,
    )
    try:
        print("waiting for headset ready...")
        teleop.wait_for_ready(timeout=15.0)
        print("streaming.")

        dt = 1.0 / max(args.render_hz, 1.0)
        while True:
            t_start = time.monotonic()
            snap = teleop.snapshot()

            # GUI updates
            gui_fps.content = f"**FPS**: {snap.fps:.1f}"
            gui_tick.content = f"**tick**: {snap.tick}"
            gui_l.content = _fmt_hand_readout(snap.l)
            gui_r.content = _fmt_hand_readout(snap.r)

            # Hand frames & markers
            for hand, color in ((snap.l, (60, 140, 240)),
                                (snap.r, (240, 120, 50))):
                prefix = f"quest/{hand.which_hand}"
                if not hand.connected or hand.pose_world is None:
                    continue

                pos, wxyz = _pose_to_wxyz_pos(hand.pose_world)
                server.scene.add_frame(
                    f"{prefix}/live",
                    position=pos,
                    wxyz=wxyz,
                    axes_length=0.06,
                    axes_radius=0.004,
                )
                # size-pulsing sphere reflects trigger
                radius = 0.012 + 0.010 * float(hand.trigger)
                server.scene.add_icosphere(
                    f"{prefix}/marker",
                    radius=radius,
                    color=color,
                    position=pos,
                    wxyz=wxyz,
                )

                # engaged / tracker pose
                if hand.engaged_pose is not None:
                    e_pos, e_wxyz = _pose_to_wxyz_pos(hand.engaged_pose)
                    server.scene.add_frame(
                        f"{prefix}/engaged",
                        position=e_pos,
                        wxyz=e_wxyz,
                        axes_length=0.12,
                        axes_radius=0.006,
                    )

            # pace the render loop
            elapsed = time.monotonic() - t_start
            if elapsed < dt:
                time.sleep(dt - elapsed)
    except KeyboardInterrupt:
        pass
    finally:
        teleop.stop()


if __name__ == "__main__":
    main()
