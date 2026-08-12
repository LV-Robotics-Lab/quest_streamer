from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_openxr_recovery.py"
SPEC = importlib.util.spec_from_file_location("verify_openxr_recovery", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


def snapshot(session: str) -> SimpleNamespace:
    return SimpleNamespace(provider_session_id=session, source_stale=session == "old")


class FakeTeleop:
    def __init__(self, sessions: list[str]) -> None:
        self._sessions = list(sessions)
        self._last = self._sessions[-1]
        self.last_error = None

    def snapshot(self) -> SimpleNamespace:
        if self._sessions:
            self._last = self._sessions.pop(0)
        return snapshot(self._last)


def test_launch_provider_returns_adb_activity_detail() -> None:
    completed = (
        subprocess.CompletedProcess(["adb", "forward"], 0, "", ""),
        subprocess.CompletedProcess(
            ["adb", "shell", "am", "start"],
            0,
            "Starting: Intent { cmp=com.rail.oculus.teleop/.MainActivity }\n",
            "",
        ),
    )
    with mock.patch.object(RECOVERY, "run_adb", side_effect=completed):
        detail = RECOVERY.launch_provider(9200)

    assert "Starting: Intent" in detail


def test_launch_provider_rejects_am_error_even_with_zero_status() -> None:
    completed = (
        subprocess.CompletedProcess(["adb", "forward"], 0, "", ""),
        subprocess.CompletedProcess(
            ["adb", "shell", "am", "start"],
            0,
            "Error: Activity not started\n",
            "",
        ),
    )
    with mock.patch.object(RECOVERY, "run_adb", side_effect=completed):
        with pytest.raises(RECOVERY.VerificationError, match="Activity not started"):
            RECOVERY.launch_provider(9200)


def test_wait_for_new_session_accepts_first_changed_session() -> None:
    attempts: list[str] = []
    result = RECOVERY.wait_for_new_session_with_relaunch(
        FakeTeleop(["old", "old", "new"]),
        "old",
        timeout=0.1,
        port=9200,
        relaunch_interval_s=1.0,
        launch_attempts=attempts,
    )

    assert result.provider_session_id == "new"
    assert attempts == []


def test_wait_for_new_session_retries_activity_launch() -> None:
    attempts: list[str] = []
    with mock.patch.object(
        RECOVERY, "launch_provider", return_value="retry launch"
    ) as launch:
        with pytest.raises(RECOVERY.VerificationError, match="repeated relaunches"):
            RECOVERY.wait_for_new_session_with_relaunch(
                FakeTeleop(["old"]),
                "old",
                timeout=0.03,
                port=9200,
                relaunch_interval_s=0.005,
                launch_attempts=attempts,
            )

    assert launch.call_count >= 1
    assert attempts == ["retry launch"] * launch.call_count
