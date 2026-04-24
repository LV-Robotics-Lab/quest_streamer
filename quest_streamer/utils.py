"""Small timing helpers used by the example scripts."""

import time


def precise_wait(t_end: float, slack_time: float = 0.001, time_func=time.monotonic) -> None:
    """Sleep until `t_end`, then spin-wait for the final `slack_time` seconds.

    Matches the semantics of `rel.utils.teleop_utils.precise_wait` in rwVR so
    replacing the import is a one-line change.
    """
    t_start = time_func()
    t_wait = t_end - t_start
    if t_wait > 0:
        t_sleep = t_wait - slack_time
        if t_sleep > 0:
            time.sleep(t_sleep)
        while time_func() < t_end:
            pass
