"""Common supporting code shared by the scripts in this folder.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from typing import Any, Literal, cast, override

import argparse
import asyncio
import logging
import os
import sys
from asyncio.subprocess import DEVNULL, PIPE, Process


class CommandLineError(Exception):
    pass


class InterpreterVersionError(Exception):
    pass


class SubprocessError(Exception):
    def __init__(self, *args: object, exit_code=0):
        self.exit_code = exit_code
        self.stderr = b""
        self.stdout = b""
        super().__init__(*args)

    @override
    def __str__(self):
        msg = [super().__str__()]
        labels = [("stdout:", self.stdout), ("stderr:", self.stderr)]
        for label, std in labels:
            std = std.decode(errors="ignore").strip()
            if std:
                msg.extend((f"Subprocess {label}", std))
        return "\n".join(msg)


class ValidationError(Exception):
    pass


LogLevelT = Literal["debug", "info", "warn", "error", "critical"]
_logger = logging.getLogger(__name__)
_subproc_log_func: Callable[..., None] = _logger.debug


def _subproc_log(*args):
    """Default logging function used by communicate() and wait_exec().

    This arrangement allows the subprocess logging function to be globally
    changed through set_subproc_log_func() while also allowing one-off changes
    through the ‘log’ argument of wait_exec(), including wait_exec(log=None).
    """
    _subproc_log_func(*args)


def set_subproc_log_func(log_func: Callable[..., None]):
    """Set the default logging function used by communicate() and wait_exec()."""
    global _subproc_log_func
    _subproc_log_func = log_func


def bool_var(name: str) -> bool:
    val = os.environ.get(name, "").strip()
    return bool(val) and val.lower() not in ("0", "no", "false", "off")


def configure_logging(
    level=logging.INFO,
    logger: logging.Logger | None = None,
    subproc_log: Callable[..., None] | None = None,
):
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    if logger:
        global _logger
        _logger = logger
    if subproc_log:
        set_subproc_log_func(subproc_log)


def set_logging_level(
    log_level_opt: LogLevelT, logger: logging.Logger = _logger
) -> int:
    from logging import _nameToLevel, DEBUG

    level = DEBUG if bool_var("DEBUG") else _nameToLevel[log_level_opt.upper()]
    logger.setLevel(level)
    return level


def add_config_option(parser: argparse.ArgumentParser):
    from config import APP_NAME

    parser.add_argument(
        "-c",
        "--config-file",
        required=True,
        help="{} configuration file in TOML format.".format(APP_NAME),
    )


def add_log_level_option(parser: argparse.ArgumentParser):
    from textwrap import dedent

    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warn", "error", "critical"],
        default="info",
        help=dedent(
            """\
            Set the logging level. The default is ‘info’. Debug logging is
            also turned on by setting the ‘DEBUG’ environment variable to
            a value other than ‘0’, ‘no’, ‘false’ or ‘off’.
            """
        ),
    )


def add_common_options(
    parser: argparse.ArgumentParser, opts: list[Literal["config", "log_level"]]
):
    for opt in opts:
        if opt == "config":
            add_config_option(parser)
        elif opt == "log_level":
            add_log_level_option(parser)


async def max_gather(max_concurrent: int, *coros: Coroutine[None, Any, Any]):
    """Use asyncio.gather() to run at most ‘max_concurrent’ coroutines at a time."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def sem_coro(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(sem_coro(c) for c in coros))


async def communicate(
    *args,
    input_data: bytes | bytearray | memoryview | None = None,
    log: Callable[..., None] | None = _subproc_log,
    raise_on_error=True,
    shell=False,
    stdin: int | None = DEVNULL,
    stdout: int | None = PIPE,
    stderr: int | None = PIPE,
    **kwargs,
) -> tuple[bytes, bytes, int]:
    """Wrap asyncio.create_subprocess_exec().communicate() and handle errors.

    Args:
        *args: Arguments to pass on to the ‘create_subprocess_exec’ function.
        **kwargs: Arguments to pass on to the ‘create_subprocess_exec’ function.
        input_data: Optional data to pipe to the subprocess standard input.
        log: Logging function to log the command line. Defaults to _logger.info.
        raise_on_error: Whether to raise SubprocessError if the subprocess
          returns a non-zero exit code.
        shell: Whether to execute the command through the system shell.
        stdin: Subprocess stdin. See asyncio.create_subprocess_exec().
        stdout: Subprocess stdout. See asyncio.create_subprocess_exec().
        stderr: Subprocess stderr. See asyncio.create_subprocess_exec().

    Returns:
        tuple[bytes, bytes, int]: Subprocess stdout, stderr, and exit code.
    """
    agen = aexec(
        *args,
        log=log,
        raise_on_error=raise_on_error,
        shell=shell,
        stdin=PIPE if input_data else stdin,
        stdout=stdout,
        stderr=stderr,
        **kwargs,
    )
    proc = cast(Process, await anext(agen))
    out, err = await proc.communicate(input_data)
    try:
        exit_code = cast(int, await anext(agen))
    except SubprocessError as e:
        e.stderr = err
        e.stdout = out
        raise
    return out, err, exit_code


async def wait_exec(
    *args,
    log: Callable[..., None] | None = _subproc_log,
    raise_on_error=True,
    shell=False,
    stdin: int | None = DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    **kwargs,
) -> int:
    """Create a subprocess, wait for it to exit and return its exit code.

    Args:
        *args: Arguments to pass on to the ‘create_subprocess_exec’ function.
        **kwargs: Arguments to pass on to the ‘create_subprocess_exec’ function.
        log: Logging function to log the command line. Defaults to _logger.info.
        raise_on_error: Whether to raise SubprocessError if the subprocess
          returns a non-zero exit code.
        shell: Whether to execute the command through the system shell.
        stdin: Subprocess stdin. See asyncio.create_subprocess_exec().
        stdout: Subprocess stdout. See asyncio.create_subprocess_exec().
        stderr: Subprocess stderr. See asyncio.create_subprocess_exec().

    Returns:
        int: Subprocess exit code.
    """
    agen = aexec(
        *args,
        log=log,
        raise_on_error=raise_on_error,
        shell=shell,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        **kwargs,
    )
    _proc = await anext(agen)
    exit_code = cast(int, await anext(agen))
    return exit_code


async def aexec(
    *args,
    log: Callable[..., None] | None = _subproc_log,
    raise_on_error=True,
    shell=False,
    stdin: int | None = DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    **kwargs,
) -> AsyncGenerator[Process | int, None]:
    """Wrap asyncio.create_subprocess_exec or *_shell and handle errors.

    This function is implemented as an async generator that yields twice:
    - First yields a Process object that allows the caller to e.g. call
      Process.communicate() as needed.
    - Then yields the subprocess exit code. Before this yield, if the exit
      code is not zero and the raise_on_error keyword argument is True, a
      SubprocessError exception is raised.

    Args:
        *args: Arguments to pass on to the ‘create_subprocess_exec’ function.
        **kwargs: Arguments to pass on to the ‘create_subprocess_exec’ function.
        log: Logging function to log the command line. Defaults to _logger.info.
        raise_on_error: Whether to raise SubprocessError if the subprocess
          returns a non-zero exit code.
        shell: Whether to execute the command through the system shell.
        stdin: Subprocess stdin. See asyncio.create_subprocess_exec().
        stdout: Subprocess stdout. See asyncio.create_subprocess_exec().
        stderr: Subprocess stderr. See asyncio.create_subprocess_exec().
    """
    if log:
        largs = ["+", *args] if log is print else ["+" + " %s" * len(args), *args]
        log(*largs)
    sub = asyncio.create_subprocess_shell if shell else asyncio.create_subprocess_exec
    proc = await sub(*args, stdin=stdin, stdout=stdout, stderr=stderr, **kwargs)
    yield proc
    exit_code = await proc.wait()
    if exit_code and raise_on_error:
        raise SubprocessError(
            f"‘{args[0]}’: non-zero exit code ‘{exit_code}’", exit_code
        )
    yield exit_code


async def pipe_subproc(cmd1: Sequence[str], cmd2: Sequence[str]) -> tuple[int, int]:
    """Pipe stdout of ‘cmd1’ subprocess to stdin of ‘cmd2’, and return their exit codes.

    Args:
        cmd1 (Iterable[str]): Executable arguments of 1st subprocess
        cmd2 (Iterable[str]): Executable arguments of 2nd subprocess

    Returns:
        tuple[int, int]: Exit codes of 1st and 2nd subprocesses
    """
    from asyncio import (
        as_completed,
        create_subprocess_exec,
        create_task,
        CancelledError,
        Task,
    )
    from os.path import basename

    def get_status_code(task_name: str, task: Task, other_proc: Process) -> int:
        code = 1 if task.cancelled() else task.result()
        _logger.debug(
            "%s: %s completed with code %s", pipe_subproc.__name__, task_name, code
        )
        # If a task (subprocess) failed, terminate the other one early.
        if code and other_proc.returncode is None:
            other_proc.terminate()
        return code

    rd, wr = os.pipe()
    _logger.info("+ %s | %s", " ".join(cmd1), " ".join(cmd2))
    proc1 = await create_subprocess_exec(*cmd1, stdout=wr)
    os.close(wr)
    proc2 = await create_subprocess_exec(*cmd2, stdin=rd)
    os.close(rd)
    task1, task2 = (create_task(proc1.wait()), create_task(proc2.wait()))
    name1, name2 = basename(cmd1[0]), basename(cmd2[0])

    # Subprocess exit status code. 0 indicates success. “A negative value -N
    # indicates that the child was terminated by signal N (POSIX only).”
    code1, code2 = 1, 1
    try:
        async for earliest in as_completed(  # pylint: disable=not-an-iterable
            (task1, task2)
        ):  # pyright: ignore[reportGeneralTypeIssues]
            if earliest is task1:
                code1 = get_status_code(f"task1 ({name1})", task1, proc2)
            else:
                code2 = get_status_code(f"task2 ({name2})", task2, proc1)
    except CancelledError as e:
        _logger.debug("%s: %s", pipe_subproc.__name__, e.__class__.__name__)

    return code1, code2


def check_python_version(min_major, min_minor, *, print_and_exit=False):
    """Check Python version compatibility.

    Note: Rewriting this function for compatibility with old Python versions
    like 3.5 (no f-strings, no type hint syntax) or even 2.7 is to no avail
    because the interpreter will anyway raise “syntax error” when parsing the
    caller module.
    """
    major, minor, micro, _, _ = sys.version_info
    if major < min_major or (major == min_major and minor < min_minor):
        msg = (
            "\nError: Python interpreter version '%d.%d.%d' does not meet version requirement '%d.%d' or later"
            % (major, minor, micro, min_major, min_minor)
        )
        if print_and_exit:
            print(msg, file=sys.stderr)
            sys.exit(1)
        else:
            raise InterpreterVersionError(msg)
