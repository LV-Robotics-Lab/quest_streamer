"""Delta-pose tracker extracted from rwVR's `SingleArmQuestAgent`.

The original agent was coupled to a robot end-effector: when the trigger went
from released to pressed it snapshotted both the controller pose and the robot
EE pose, then every subsequent frame it applied the controller's delta on top
of the snapshotted EE pose.

`DeltaPoseTracker` keeps that state machine but makes the "snapshotted pose"
caller-provided, so it can track *any* reference frame (a simulated end
effector, a camera, a viser frame, etc.) - not only a physical robot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from quest_streamer.frames import X_QuestWorld, X_WorldQuest
from quest_streamer.reader import HandFrame, QuestStreamer


@dataclass
class TrackerStep:
    """One output of `DeltaPoseTracker.step()`.

    `X_WorldRef_next` is the updated world-frame reference pose while the
    trigger is held. `trigger`, `grip`, `joystick`, `buttons` mirror the raw
    Quest inputs for that step, in case the caller wants to route them (e.g.
    map `grip` to a gripper command).
    """

    X_WorldRef_next: np.ndarray
    hand: HandFrame
    just_engaged: bool
    just_released: bool


class DeltaPoseTracker:
    """Trigger-engaged delta-pose tracker for a single Quest controller.

    State machine:

    * Trigger released                 -> `active` is False; `step()` returns `None`.
    * Trigger crosses rising edge      -> snapshot the current controller pose and
                                          the caller-provided `X_WorldRef_current`;
                                          returns a step whose next pose equals the
                                          snapshot (i.e. zero delta on engage).
    * Trigger held                     -> returns a step whose next pose is the
                                          snapshot composed with the controller's
                                          delta since engagement.
    * Trigger crosses falling edge     -> drops the snapshot; subsequent calls
                                          require a fresh engage.
    """

    def __init__(
        self,
        streamer: QuestStreamer,
        which_hand: str,
        translation_scaling_factor: float = 1.0,
        trigger_threshold: float = 0.5,
    ) -> None:
        if which_hand not in ("l", "r"):
            raise ValueError(f"which_hand must be 'l' or 'r', got {which_hand!r}")

        self._streamer = streamer
        self.which_hand = which_hand
        self.translation_scaling_factor = float(translation_scaling_factor)
        self.trigger_threshold = float(trigger_threshold)

        self.active: bool = False
        self._X_QuestHandle_ref: Optional[np.ndarray] = None
        self._X_WorldRef_ref: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ api

    def step(self, X_WorldRef_current: np.ndarray) -> Optional[TrackerStep]:
        """Advance one tick.

        Args:
            X_WorldRef_current: 4x4 world-frame pose the caller wants to drive
                with the controller. Only read on the engage transition.

        Returns:
            `None` when the headset has no data yet or the trigger is released;
            a `TrackerStep` otherwise.
        """
        hand = self._streamer.read_hand(self.which_hand, in_world_frame=False)
        if hand is None:
            return None

        pressed = hand.trigger > self.trigger_threshold

        if not pressed:
            was_active = self.active
            self.active = False
            self._X_QuestHandle_ref = None
            self._X_WorldRef_ref = None
            if was_active:
                return TrackerStep(
                    X_WorldRef_next=np.asarray(X_WorldRef_current, dtype=np.float64).copy(),
                    hand=hand,
                    just_engaged=False,
                    just_released=True,
                )
            return None

        just_engaged = not self.active
        if just_engaged:
            self.active = True
            self._X_QuestHandle_ref = hand.pose.copy()
            self._X_WorldRef_ref = np.asarray(X_WorldRef_current, dtype=np.float64).copy()
            return TrackerStep(
                X_WorldRef_next=self._X_WorldRef_ref.copy(),
                hand=hand,
                just_engaged=True,
                just_released=False,
            )

        assert self._X_QuestHandle_ref is not None
        assert self._X_WorldRef_ref is not None

        X_QuestHandle_curr = hand.pose
        dpos_in_Quest = X_QuestHandle_curr[:3, 3] - self._X_QuestHandle_ref[:3, 3]
        drot_in_Quest = X_QuestHandle_curr[:3, :3] @ np.linalg.inv(self._X_QuestHandle_ref[:3, :3])

        dpos_in_World = X_QuestWorld[:3, :3] @ dpos_in_Quest + X_QuestWorld[:3, 3]
        drot_in_World = X_QuestWorld[:3, :3] @ drot_in_Quest @ X_WorldQuest[:3, :3]

        X_WorldRef_next = np.eye(4)
        X_WorldRef_next[:3, 3] = (
            dpos_in_World * self.translation_scaling_factor + self._X_WorldRef_ref[:3, 3]
        )
        X_WorldRef_next[:3, :3] = drot_in_World @ self._X_WorldRef_ref[:3, :3]

        return TrackerStep(
            X_WorldRef_next=X_WorldRef_next,
            hand=hand,
            just_engaged=False,
            just_released=False,
        )

    def reset(self) -> None:
        """Force a fresh engage on the next trigger press."""
        self.active = False
        self._X_QuestHandle_ref = None
        self._X_WorldRef_ref = None
