"""Coordinate-frame conventions used when streaming Quest data.

The raw pose returned by `OculusReader.get_transformations_and_buttons()` is
expressed in the Quest's own reference frame (Y-up, -Z-forward, X-right when
the headset boots). Most robotics code expects a Z-up world frame. The
transform below rotates -90 degrees about the X axis, mapping the Quest frame
onto a conventional Z-up "world" frame.

    world_point = X_QuestWorld[:3, :3] @ quest_point + X_QuestWorld[:3, 3]

This matches what `SingleArmQuestAgent` in rwVR used, so data captured with the
original codebase stays numerically identical.
"""

import numpy as np
from scipy.spatial.transform import Rotation as R

X_WorldQuest: np.ndarray = np.eye(4)
X_WorldQuest[:3, :3] = R.from_euler("X", [-90], degrees=True).as_matrix()

X_QuestWorld: np.ndarray = np.linalg.inv(X_WorldQuest)
