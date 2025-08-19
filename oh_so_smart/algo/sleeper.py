"""Supporting time tracking classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio


class RegularSleeper:
    """Sleep for regular periods and wake up at scheduled times, avoiding drift.

    Naively repeatedly sleeping causes cumulative time drift, for example:
    12:10:00.000 - Sleep 1s
    12:10:01.010 - Sleep 1s - 10ms cumulative drift
    12:10:02.025 - Sleep 1s - 25ms cumulative drift
    12:10:03.035 - Sleep 1s - 35ms cumulative drift
    12:10:04.050 - Sleep 1s - 50ms cumulative drift

    This class uses asyncio loop.call_at() to avoid cumulative drift, for example:
    12:10:00.000 - Sleep 1s
    12:10:01.010 - Sleep 1s - 10ms approximately constant drift
    12:10:02.015 - Sleep 1s - 10ms approximately constant drift
    12:10:03.010 - Sleep 1s - 10ms approximately constant drift
    12:10:04.015 - Sleep 1s - 10ms approximately constant drift
    """

    def __init__(self, interval_sec: float, loop: asyncio.AbstractEventLoop):
        self._interval_sec = interval_sec
        self._loop = loop
        self._t0 = 0.0

    async def sleep(self):
        event = asyncio.Event()
        now = self._loop.time()
        if not self._t0:
            self._t0 = now
        n_intervals = (now - self._t0) // self._interval_sec
        wakeup_time = self._t0 + (n_intervals + 1) * self._interval_sec
        # Note: call_at(ts, cb) calls the callback cb straight away if given a
        # timestamp ts in the past.
        handle = self._loop.call_at(wakeup_time, event.set)
        # Note: event.wait() is interrupted with CancelledError if the caller
        # task is cancelled, e.g. if a sibling TaskGroup task raises an error.
        try:
            await event.wait()
        finally:
            handle.cancel()
