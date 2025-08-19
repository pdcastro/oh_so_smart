"""Supporting signal handling classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations
from collections.abc import Iterable
import asyncio
import logging
import signal


_LOGGER = logging.getLogger(__name__)


class SignalException(Exception):
    def __init__(self, sig_num: int):
        self.sig_num = sig_num
        try:
            self.sig_name = signal.Signals(sig_num).name
        except ValueError:
            self.sig_name = "Uknown"

        super().__init__(self.sig_name, self.sig_num)

    def __str__(self) -> str:
        return f"{self.sig_name} ({self.sig_num})"


class SignalMonitor:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._sig_exc: SignalException | None = None
        self._sig_event = asyncio.Event()
        self._sig_event_wait_task: asyncio.Task | None = None
        self._sig_event_wait_task_done = asyncio.Event()

    @classmethod
    def instance(
        cls,
        loop: asyncio.AbstractEventLoop,
        for_signals: Iterable[signal.Signals] = (signal.SIGINT, signal.SIGTERM),
    ) -> SignalMonitor:
        signal_monitor = cls(loop)
        signal_monitor.add_handlers(for_signals)
        return signal_monitor

    def add_handlers(self, signals: Iterable[signal.Signals]):
        for sig in signals:
            signum = int(sig)
            self._loop.add_signal_handler(signum, self._sig_handler, signum)

    async def cancel(self):
        if self._sig_event_wait_task and self._sig_event_wait_task.cancel():
            await self._sig_event_wait_task_done.wait()

    async def monitor(self, *, shielded: bool):
        self._sig_event_wait_task = asyncio.create_task(
            self._sig_event.wait(),
            name="sig_event_wait_task",
        )
        self._sig_event_wait_task.add_done_callback(
            lambda _task: self._sig_event_wait_task_done.set()
        )
        if shielded:
            await asyncio.shield(self._sig_event_wait_task)
        else:
            await self._sig_event_wait_task

        raise self._sig_exc or Exception(
            f"{type(self).__name__} {self.monitor.__name__}(): inconsistent state"
        )

    def _sig_handler(self, signum: int, _frame: object | None = None):
        self._sig_exc = SignalException(signum)
        self._sig_event.set()
        _LOGGER.debug(
            "%s %s(): %s",
            type(self).__name__,
            self._sig_handler.__name__,
            self._sig_exc,
        )
