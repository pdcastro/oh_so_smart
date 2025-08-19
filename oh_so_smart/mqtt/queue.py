"""MsgQueue class - a producer/consumer container for MQTT messages.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio


class MsgQueue[T](asyncio.Queue):
    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 0):
        self.__loop = loop
        self._cancelled = False
        super().__init__(maxsize)

    async def get(self, timeout_sec: float | None = None) -> T:
        # Note that if a task gets cancelled (e.g. by a TaskGroup) while the task is
        # awaiting here, the await gets interrupted and CancelledError is raised here.
        return await asyncio.wait_for(super().get(), timeout_sec)

    def put_threadsafe(self, item: T, timeout_sec: float | None = None):
        """Put an item in the queue, threadsafe.

        Args:
            item (T): Item to put in the queue
            timeout_sec (float | None, optional): If zero, perform a non-blocking
              operation that may raise `asyncio.QueueFull`. If None, block indefinitely
              (while the queue is full). Otherwise, block up to timeout_sec seconds
              (while the queue is full) and then raise TimeoutError.

        Raises:
            asyncio.QueueFull: If the queue is full and timeout_sec is zero.
            TimeoutError: On timeout, if timeout_sec is neither zero nor None.
        """

        async def put():
            # timeout_sec is None -> block indefinetely (until the queue is not full)
            # timeout_sec is zero -> do not block (may raise QueueFull)
            return self.put_nowait(item) if timeout_sec == 0 else await self.put(item)

        future = asyncio.run_coroutine_threadsafe(put(), self.__loop)
        future.result(timeout_sec or None)

    def put_nowait_threadsafe(self, item: T):
        self.put_threadsafe(item, timeout_sec=0)

    def as_list(self) -> list[T]:
        """Get a list of messages currently in the queue."""
        msgs = []
        while True:
            try:
                msgs.append(self.get_nowait())
            except asyncio.QueueEmpty:
                break

        return msgs
