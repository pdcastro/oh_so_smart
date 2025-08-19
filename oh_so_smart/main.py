"""Top-level code that starts the asyncio loop and handles errors and signals.

The main() method is called by the package entrypoint code in __main__.py.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio
import logging
import signal

from .algo.error import fmt_exception_group
from .algo.signal_monitor import SignalException, SignalMonitor
from .cmdline_parser import CmdArgs, parse_command_line
from .config.schema import ConfigError, CoreConfig
from .mqtt.manager import MQTTManager
from .mqtt.queue import MsgQueue
from .products.factory import Manager, ProductObjects, make_objects
from .sensors.manager import TemperatureSensorManager
from .switches.manager import HassSwitchManager


_LOGGER = logging.getLogger(__name__)


def _inspect_task_result(task: asyncio.Task):
    prefix = f"Top level handler: Task '{task.get_name()}'"
    try:
        res = task.result()
        _LOGGER.info("%s exited with result '%s'", prefix, res)
    except asyncio.InvalidStateError as e:
        _LOGGER.error("%s: %s", prefix, type(e).__name__)
    except asyncio.CancelledError:
        _LOGGER.warning("%s was cancelled", prefix)
    except (BaseExceptionGroup, Exception) as e:
        e.add_note(f"Task '{task.get_name()}'")
        # BaseExceptionGroup may contain instances of Exception.
        if isinstance(e, BaseExceptionGroup):
            _LOGGER.error(fmt_exception_group(e, f"{prefix} error:"))
        else:
            _LOGGER.error("%s error: %s", prefix, e)


def _debug_pending_tasks():
    all_tasks = asyncio.all_tasks()
    if len(all_tasks) > 1:  # Ignore the main task
        _LOGGER.debug(
            "%s(): Inspecting %d pending tasks: %s",
            _debug_pending_tasks.__name__,
            len(all_tasks),
            ", ".join([t.get_name() for t in all_tasks]),
        )
        for task in all_tasks:
            print(f"Task '{task.get_name()}' stack:")
            task.print_stack()


def _debug_pending_threads():
    import threading

    all_threads = threading.enumerate()
    if len(all_threads) > 1:  # Ignore the main thread
        _LOGGER.debug(
            "%s(): Inspecting %d pending threads",
            _debug_pending_threads.__name__,
            len(all_threads),
        )
        for t in all_threads:
            print(f"Thread {t.name=} {t.ident=} {t.native_id=}': {t}")


def _force_process_exit():
    """Send SIGKILL to self as sometimes threads get stuck at join() and sys.exit() is not enough."""
    import os

    _LOGGER.warning(
        "%s %s(): forcing process exit",
        __name__,
        _force_process_exit.__name__,
    )
    os.kill(os.getpid(), signal.SIGKILL)


async def _create_manager_tasks(args: CmdArgs, loop: asyncio.AbstractEventLoop):
    prod: ProductObjects = make_objects(args, loop)
    config: CoreConfig = prod.config
    send_queue = MsgQueue(loop, maxsize=50)
    recv_queue = MsgQueue(loop, maxsize=50)
    managers: list[Manager] = []
    if config.mqtt:
        managers.append(MQTTManager(loop, send_queue, recv_queue, config.mqtt))
        if prod.temperature_sensor_groups:
            managers.append(
                TemperatureSensorManager(
                    loop, send_queue, prod.temperature_sensor_groups
                )
            )
        if prod.switch_groups:
            managers.append(
                HassSwitchManager(send_queue, recv_queue, prod.switch_groups)
            )
    if prod.manager:
        managers.append(prod.manager)

    log_prefix = f"{__name__} {_create_manager_tasks.__name__}()"

    tasks: list[asyncio.Task] = []
    try:
        async with asyncio.TaskGroup() as tg:
            for manager in managers:
                tasks.append(
                    tg.create_task(manager.start(), name=type(manager).__name__)
                )
        _LOGGER.debug("%s: TaskGroup finished", log_prefix)

    except ExceptionGroup:
        for task in tasks:
            _inspect_task_result(task)
        raise

    _LOGGER.debug("%s: exiting", log_prefix)


async def _create_top_level_tasks(
    args: CmdArgs, signal_monitor: SignalMonitor, loop: asyncio.AbstractEventLoop
):
    async with asyncio.TaskGroup() as tg:
        # Note: the signal monitor task is shielded from cancellation
        tg.create_task(
            signal_monitor.monitor(shielded=True),
            name=type(signal_monitor).__name__,
        )
        tg.create_task(
            _create_manager_tasks(args, loop),
            name=f"{__name__}_{_create_manager_tasks.__name__}()",
        )


async def _handle_top_level_exceptions(args: CmdArgs) -> int:
    exit_code = 0
    loop = asyncio.get_running_loop()
    signal_monitor: SignalMonitor | None = None
    try:
        signal_monitor = SignalMonitor.instance(loop)
        await _create_top_level_tasks(args, signal_monitor, loop)

    except* (SignalException, KeyboardInterrupt) as group:
        exit_code = 2
        _LOGGER.warning(
            fmt_exception_group(group, "Process interrupted by the OS or the user:")
        )

    except* Exception as group:
        exit_code = 1
        _LOGGER.error(fmt_exception_group(group, "Exiting with exceptions:"))

        no_stack_trace_exceptions = (ConfigError, FileNotFoundError)
        if not any(
            isinstance(e, f)
            for e in group.exceptions  # pylint: disable=no-member
            for f in no_stack_trace_exceptions
        ):
            from traceback import format_exc

            exc_str = format_exc()
            _LOGGER.error(exc_str)

    # Schedule a forceful process exit. In normal conditions, the asyncio
    # loop exits before the scheduled forceful process exit gets to execute.
    loop.call_later(5, _force_process_exit)

    if signal_monitor:
        await signal_monitor.cancel()

    if __debug__:
        _debug_pending_tasks()

    return exit_code


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        level=logging.DEBUG if __debug__ else logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_command_line()

    exit_code = asyncio.run(_handle_top_level_exceptions(args))
    if __debug__:
        _debug_pending_threads()

    return exit_code
