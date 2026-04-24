"""Live preview of the passthrough camera stream using an OpenCV window.

Prereqs:
    # 1. Headset: open `quest_camera_streamer` APK, tap Start streaming.
    # 2. PC:
    adb forward tcp:9100 tcp:9100

Run:
    uv pip install opencv-python      # one-time
    uv run python examples/camera_preview.py

Press `q` in the window to quit.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from quest_streamer import CameraStreamer


def main() -> None:
    import cv2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--scale", type=float, default=0.5,
                        help="Display scale factor (1.0 = native 1280 wide per eye).")
    args = parser.parse_args()

    with CameraStreamer(host=args.host, port=args.port) as cam:
        print(f"waiting for stream at {args.host}:{args.port}...")
        if not cam.wait_for_ready(timeout=15.0):
            print("no frames in 15s. Is the APK streaming? adb forward set?")
            return

        window = "quest_camera (L|R)"
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        print("streaming. press 'q' in the window to quit.")

        last_tick = -1
        last_fps_report = time.monotonic()
        while True:
            snap = cam.snapshot()
            if snap.tick == last_tick:
                # No new frame since last poll; sleep a hair.
                if cv2.waitKey(5) & 0xFF == ord("q"):
                    break
                continue
            last_tick = snap.tick

            frames = []
            for cf in (snap.l, snap.r):
                if cf.frame is not None:
                    if args.scale != 1.0:
                        f = cv2.resize(cf.frame, None, fx=args.scale, fy=args.scale,
                                       interpolation=cv2.INTER_AREA)
                    else:
                        f = cf.frame
                    frames.append(f)

            if not frames:
                continue
            # pad to equal height then hstack
            max_h = max(f.shape[0] for f in frames)
            padded = [
                cv2.copyMakeBorder(f, 0, max_h - f.shape[0], 0, 0, cv2.BORDER_CONSTANT)
                for f in frames
            ]
            mosaic = np.hstack(padded) if len(padded) > 1 else padded[0]
            cv2.imshow(window, mosaic)

            now = time.monotonic()
            if now - last_fps_report > 2.0:
                print(f"snapshot fps={snap.fps:.1f} tick={snap.tick} "
                      f"L seq={snap.l.sequence_id} R seq={snap.r.sequence_id}")
                last_fps_report = now

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
