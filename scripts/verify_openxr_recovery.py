#!/usr/bin/env python3
"""Input-only physical gate for OpenXR stale/restart/release recovery."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from quest_streamer import QuestTeleop

PACKAGE = "com.rail.oculus.teleop"
ACTIVITY = f"{PACKAGE}/.MainActivity"
ADB_BIN = os.environ.get("QUEST_ADB_BIN") or "adb"


class VerificationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openxr-host", default="127.0.0.1")
    parser.add_argument("--openxr-port", type=int, default=9200)
    parser.add_argument("--frequency", type=float, default=60.0)
    parser.add_argument("--source-stale-timeout-s", type=float, default=0.3)
    parser.add_argument("--operator-timeout-s", type=float, default=30.0)
    parser.add_argument("--recovery-timeout-s", type=float, default=20.0)
    parser.add_argument("--restart-settle-s", type=float, default=3.0)
    parser.add_argument("--relaunch-interval-s", type=float, default=5.0)
    parser.add_argument("--summary-json", type=Path)
    return parser.parse_args()


def run_adb(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ADB_BIN, *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def launch_provider(port: int) -> str:
    run_adb("forward", f"tcp:{port}", f"tcp:{port}")
    result = run_adb(
        "shell",
        "am",
        "start",
        "-n",
        ACTIVITY,
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
        "--ez",
        "enable_camera",
        "false",
        "--ez",
        "enable_hand_telemetry",
        "false",
        check=False,
    )
    detail = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part.strip()
    )
    if result.returncode != 0 or "Error:" in detail:
        raise VerificationError(f"failed to launch provider: {detail or result.returncode}")
    return detail


def tracked(hand) -> bool:
    return all(
        (
            hand.connected,
            hand.pose_source == "tracked_remote",
            hand.controller_active is True,
            hand.position_valid is True,
            hand.orientation_valid is True,
            hand.position_tracked is True,
            hand.orientation_tracked is True,
        )
    )


def wait_for(teleop, predicate, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    last = teleop.snapshot()
    while time.monotonic() < deadline:
        last = teleop.snapshot()
        if teleop.last_error is not None:
            raise VerificationError(f"provider error during {description}: {teleop.last_error}")
        if predicate(last):
            return last
        time.sleep(0.01)
    raise VerificationError(
        f"timed out waiting for {description}; "
        f"session={last.provider_session_id!r} stale={last.source_stale} "
        f"left_connected={last.l.connected} left_trigger={last.l.trigger:.3f} "
        f"left_engaged={last.l.engaged} right_connected={last.r.connected} "
        f"right_trigger={last.r.trigger:.3f}"
    )


def wait_for_new_session_with_relaunch(
    teleop,
    old_session: str,
    *,
    timeout: float,
    port: int,
    relaunch_interval_s: float,
    launch_attempts: list[str],
):
    deadline = time.monotonic() + timeout
    next_relaunch = time.monotonic() + relaunch_interval_s
    last = teleop.snapshot()
    while time.monotonic() < deadline:
        last = teleop.snapshot()
        if teleop.last_error is not None:
            raise VerificationError(
                f"provider error while waiting for restarted session: {teleop.last_error}"
            )
        if last.provider_session_id and last.provider_session_id != old_session:
            return last
        now = time.monotonic()
        if now >= next_relaunch:
            try:
                detail = launch_provider(port)
            except VerificationError as exc:
                detail = str(exc)
            launch_attempts.append(detail or "launch command completed without output")
            next_relaunch = now + relaunch_interval_s
        time.sleep(0.01)
    raise VerificationError(
        "timed out waiting for a new provider session after repeated relaunches; "
        f"session={last.provider_session_id!r} stale={last.source_stale}"
    )


def prompt(label: str, message: str) -> None:
    print(f"ACTION {label}: {message}", flush=True)


def cleanup_provider(port: int) -> list[str]:
    errors: list[str] = []
    commands = (
        ("shell", "am", "force-stop", PACKAGE),
        ("forward", "--remove", f"tcp:{port}"),
    )
    for command in commands:
        try:
            result = run_adb(*command, check=False)
        except OSError as exc:
            errors.append(f"adb {' '.join(command)}: {exc}")
            continue
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            errors.append(f"adb {' '.join(command)}: {detail or result.returncode}")
    return errors


def collect_adb_diagnostics() -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    commands = {
        "pidof": ("shell", "pidof", PACKAGE),
        "activity_top": ("shell", "dumpsys", "activity", "top"),
        "openxr_logcat": (
            "logcat",
            "-d",
            "-t",
            "200",
            "-s",
            "QuestTelemetry",
            "OpenXRLoader",
        ),
    }
    for label, command in commands.items():
        try:
            result = run_adb(*command, check=False)
        except OSError as exc:
            diagnostics[label] = {"error": str(exc)}
            continue
        diagnostics[label] = {
            "returncode": result.returncode,
            "stdout": result.stdout.strip()[-12000:],
            "stderr": result.stderr.strip()[-4000:],
        }
    return diagnostics


def main() -> int:
    args = parse_args()
    for name in (
        "frequency",
        "source_stale_timeout_s",
        "operator_timeout_s",
        "recovery_timeout_s",
        "restart_settle_s",
        "relaunch_interval_s",
    ):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value <= 0:
            print(f"{name} must be finite and > 0", file=sys.stderr)
            return 2

    summary: dict[str, object] = {
        "schema": "nero.quest_openxr_recovery.v2",
        "mode": "INPUT_ONLY_NO_ROBOT_COMMAND",
        "accepted": False,
        "source_stale_timeout_s": args.source_stale_timeout_s,
        "restart_settle_s": args.restart_settle_s,
    }
    launch_attempts: list[str] = []

    print(
        "OPERATOR PLAN: Keep the headset worn for the whole trial. After HOLD_LEFT, "
        "keep the LEFT Trigger held while the view temporarily disappears. When "
        "passthrough returns, keep holding for 2 more seconds, release, wait 2 "
        "seconds, press LEFT once for 1 second, then release. Terminal prompts are "
        "optional cues; you do not need to read them during the restart.",
        flush=True,
    )

    try:
        launch_attempts.append(
            launch_provider(args.openxr_port) or "initial launch completed without output"
        )
        with QuestTeleop(
            frequency=args.frequency,
            controller_backend="openxr-tcp",
            openxr_host=args.openxr_host,
            openxr_port=args.openxr_port,
            source_stale_timeout_s=args.source_stale_timeout_s,
        ) as teleop:
            ready = wait_for(
                teleop,
                lambda snap: tracked(snap.l) and tracked(snap.r),
                args.recovery_timeout_s,
                "two tracked controllers",
            )
            summary["initial_provider_session_id"] = ready.provider_session_id

            prompt("RELEASE_BOTH", "Release both Index Triggers and keep controllers tracked.")
            wait_for(
                teleop,
                lambda snap: (
                    tracked(snap.l)
                    and tracked(snap.r)
                    and snap.l.trigger <= 0.2
                    and snap.r.trigger <= 0.2
                    and not snap.l.engaged
                    and not snap.r.engaged
                ),
                args.operator_timeout_s,
                "both triggers released",
            )

            prompt(
                "HOLD_LEFT",
                "Press and KEEP HOLDING LEFT through restart. After passthrough "
                "returns, hold 2 seconds, release, wait 2 seconds, press LEFT "
                "again for 1 second, then release.",
            )
            engaged = wait_for(
                teleop,
                lambda snap: (
                    tracked(snap.l)
                    and snap.l.trigger > 0.5
                    and snap.l.engaged
                ),
                args.operator_timeout_s,
                "left trigger engage",
            )
            old_session = engaged.provider_session_id
            print("PHASE force-stopping provider while LEFT Trigger is held", flush=True)
            run_adb("shell", "am", "force-stop", PACKAGE)

            stale = wait_for(
                teleop,
                lambda snap: (
                    snap.source_stale
                    and not snap.l.connected
                    and not snap.r.connected
                    and not snap.l.engaged
                    and not snap.r.engaged
                ),
                max(2.0, args.source_stale_timeout_s * 4.0),
                "stale disconnected snapshot",
            )
            stale_age_s = (
                time.monotonic_ns() - int(stale.receive_monotonic_ns)
            ) / 1e9
            summary["stale_last_receive_age_s"] = stale_age_s
            if stale_age_s > args.source_stale_timeout_s + 0.20:
                raise VerificationError(
                    f"stale transition was too slow: last-receive age {stale_age_s:.3f}s"
                )

            print(
                f"PHASE waiting {args.restart_settle_s:.1f}s for OpenXR cleanup; "
                "KEEP LEFT Trigger held",
                flush=True,
            )
            time.sleep(args.restart_settle_s)
            print("PHASE relaunching provider; KEEP LEFT Trigger held", flush=True)
            launch_attempts.append(
                launch_provider(args.openxr_port)
                or "restart launch completed without output"
            )
            new_session_snapshot = wait_for_new_session_with_relaunch(
                teleop,
                str(old_session),
                timeout=args.recovery_timeout_s,
                port=args.openxr_port,
                relaunch_interval_s=args.relaunch_interval_s,
                launch_attempts=launch_attempts,
            )
            summary["recovered_provider_session_id"] = (
                new_session_snapshot.provider_session_id
            )
            recovered = wait_for(
                teleop,
                lambda snap: (
                    snap.provider_session_id == new_session_snapshot.provider_session_id
                    and tracked(snap.l)
                ),
                args.recovery_timeout_s,
                "tracked left controller in the new provider session",
            )
            new_session = recovered.provider_session_id
            summary["right_tracked_at_initial_recovery"] = tracked(recovered.r)
            summary["held_trigger_after_restart"] = recovered.l.trigger
            summary["engaged_while_held_after_restart"] = recovered.l.engaged
            if recovered.l.trigger <= 0.5:
                raise VerificationError(
                    "LEFT Trigger was not held through restart; repeat the operator trial"
                )
            if recovered.l.engaged or recovered.l.just_engaged:
                raise VerificationError("held Trigger re-engaged across provider restart")

            held_until = time.monotonic() + 0.5
            held_frames = 0
            while time.monotonic() < held_until:
                held = teleop.snapshot()
                if held.provider_session_id != new_session or held.l.trigger <= 0.5:
                    raise VerificationError("LEFT Trigger was released during hold proof")
                if held.l.engaged or held.l.just_engaged:
                    raise VerificationError("held Trigger engaged before release")
                held_frames += 1
                time.sleep(0.01)
            summary["held_disarmed_observations"] = held_frames

            prompt("RELEASE_LEFT", "Release the LEFT Index Trigger now.")
            post_release = wait_for(
                teleop,
                lambda snap: (
                    snap.provider_session_id == new_session
                    and snap.l.trigger <= 0.2
                    and not snap.l.engaged
                ),
                args.operator_timeout_s,
                "left release after restart",
            )
            if post_release.l.engaged:
                raise VerificationError("release did not leave the left hand disarmed")

            prompt(
                "RESTORE_BOTH",
                "Keep both Triggers released; wake and visibly track both controllers.",
            )
            wait_for(
                teleop,
                lambda snap: (
                    snap.provider_session_id == new_session
                    and tracked(snap.l)
                    and tracked(snap.r)
                    and snap.l.trigger <= 0.2
                    and snap.r.trigger <= 0.2
                    and not snap.l.engaged
                    and not snap.r.engaged
                ),
                args.operator_timeout_s,
                "both controllers restored and released",
            )

            prompt("PRESS_LEFT_AGAIN", "Press the LEFT Index Trigger once more.")
            fresh_engage = wait_for(
                teleop,
                lambda snap: (
                    snap.provider_session_id == new_session
                    and tracked(snap.l)
                    and tracked(snap.r)
                    and snap.l.trigger > 0.5
                    and snap.l.engaged
                    and snap.l.just_engaged
                ),
                args.operator_timeout_s,
                "fresh left engage after release",
            )
            summary["fresh_engage_after_release"] = fresh_engage.l.just_engaged

            prompt("RELEASE_LEFT_FINAL", "Release the LEFT Index Trigger to finish.")
            wait_for(
                teleop,
                lambda snap: snap.l.trigger <= 0.2 and not snap.l.engaged,
                args.operator_timeout_s,
                "final left release",
            )
            summary["accepted"] = True
    except (OSError, subprocess.CalledProcessError, VerificationError) as exc:
        summary["failure"] = str(exc)
        summary["adb_diagnostics"] = collect_adb_diagnostics()
    finally:
        summary["launch_attempts"] = launch_attempts
        cleanup_errors = cleanup_provider(args.openxr_port)
        summary["provider_stopped"] = not cleanup_errors
        if cleanup_errors:
            summary["cleanup_errors"] = cleanup_errors
            summary["accepted"] = False
            summary.setdefault("failure", "provider cleanup failed")

    encoded = json.dumps(summary, sort_keys=True, indent=2) + "\n"
    if args.summary_json is not None:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(encoded, encoding="utf-8")
    print(encoded, end="", flush=True)
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
