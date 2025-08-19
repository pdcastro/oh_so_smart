"""Start the app in a subprocess and conditionally restart it on errors.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio
import logging
import os
import sys
from os.path import abspath, basename, dirname


_LOGGER = logging.getLogger(__name__)


def bool_var(name: str) -> bool:
    val = os.environ.get(name, "").strip()
    return bool(val) and val.lower() not in ("0", "no", "false", "off")


def is_debug():
    return __debug__ or bool_var("DEBUG")


async def start_app():
    _LOGGER.debug("argv[0] is '%s'", sys.argv[0])
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m" if is_debug() else "-Om",
        basename(dirname(abspath(__file__))),
        *sys.argv[1:],  # Pass any other command-line arguments through
        stdin=None,
        stdout=None,
        stderr=None,
    )

    exc: asyncio.CancelledError | None = None
    for _ in range(2):
        try:
            await proc.wait()
            log = _LOGGER.error if proc.returncode else _LOGGER.warning
            log("App exited with code '%s'", proc.returncode)
            break
        except asyncio.CancelledError as e:
            _LOGGER.info(type(e).__name__)
            exc = e
            # CTRL-C cancels wait() before the child terminates, so wait once more
            continue

    if exc:
        raise exc


async def run():
    restart_count = 0
    restart_max = 3
    while restart_count < restart_max:
        if restart_count:
            _LOGGER.warning("Restarting app (%s of %s)", restart_count, restart_max)
        else:
            _LOGGER.info("Starting app")

        try:
            await start_app()
        except asyncio.CancelledError:
            _LOGGER.info("Execution cancelled (typically CTRL-C): Aborting")
            break

        wait_sec = 60
        _LOGGER.info("Waiting %d seconds before app restart", wait_sec)
        await asyncio.sleep(wait_sec)

        restart_count += 1
    else:
        _LOGGER.error("Too many app restarts: Aborting")

    _LOGGER.info("Exiting")


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(run())
