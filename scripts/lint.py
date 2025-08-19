#!/usr/bin/env python3
"""Run code linters and formatters: ruff, pylint, pyright and shellcheck.

See additional documentation with ‘lint.py --help’ or in the _parse_cmd_line()
function in this file.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import argparse
import asyncio
import logging
import os
import os.path
import shutil
import sys
import textwrap
from dataclasses import dataclass
from glob import glob
from typing import Final, Literal

from common import (
    CommandLineError,
    SubprocessError,
    ValidationError,
    check_python_version,
    communicate,
    configure_logging,
    wait_exec,
)
from config import DEFAULT_PROJECT_DIR

_logger = logging.getLogger(__name__)


# These tools are listed in execution order.
AVAILABLE_TOOLS = [
    "all",  # Shorthand for running all available tools
    "ruff-check",
    "pylint",
    "pyright",
    "ruff-format",
    "shellcheck",
    "check-uncommitted",
]


@dataclass
class Opts:
    # Formatter and linter tools selected on the command line.
    tools: list[str]

    def __post_init__(self):
        """Validate and rewrite the command line options."""
        selected_set = set(self.tools)
        available_set = set(AVAILABLE_TOOLS)
        if selected_set:
            unknown_tools = selected_set - available_set
            if unknown_tools:
                raise CommandLineError(f"Unknown tool(s): {unknown_tools}")
            if "all" in selected_set:
                selected_set = available_set - {"all"}
        else:
            # By default, all tools except ‘check-uncommitted’
            selected_set = available_set - {"all", "check-uncommitted"}

        # Preserve the listing order of tools in ‘available_tools’
        self.tools = [tool for tool in AVAILABLE_TOOLS if tool in selected_set]


class ToolNotFound(Exception):
    pass


PIP_INSTALL_INSTRUCTIONS: Final = textwrap.dedent(
    """
    It can be installed with:
    $ python -m venv venv
    $ ./venv/bin/pip install -r requirements_dev.txt
    """
)


def _check_which(tool: str, install_instructions=PIP_INSTALL_INSTRUCTIONS) -> str:
    for prefix in (["venv", "bin"], ["env", "bin"], []):
        if path := shutil.which(os.path.join(*prefix, tool)):
            return path

    raise ToolNotFound(f"‘{tool}’ not found. {install_instructions}")


def _print_header(tool: str):
    print()
    msg = "\n".join(["", "-" * 70, f"Running {tool}...", "-" * 70])
    _logger.info(msg)


async def _run_ruff(subcommand: Literal["check", "format"]):
    ruff = _check_which("ruff")
    await wait_exec(ruff, "--version")
    await wait_exec(
        ruff,
        subcommand,
        "oh_so_smart",
        *glob(os.path.join("scripts", "*.py")),
    )


async def _run_ruff_check():
    await _run_ruff("check")


async def _run_ruff_format():
    await _run_ruff("format")


async def _run_pylint():
    pylint = _check_which("pylint")
    await wait_exec(pylint, "--version")
    await wait_exec(
        pylint,
        "--load-plugins",
        "pylint_pydantic",
        "-v",
        "oh_so_smart",
        *glob(os.path.join("scripts", "*.py")),
    )


async def _run_pyright():
    python = _check_which("python")
    pyright = "pyright"
    try:
        cmd = [
            _check_which(
                pyright,
                install_instructions="It can be installed with:\n"
                "$ npm install -g pyright",
            )
        ]
        await wait_exec(*cmd, "--version")
    except ToolNotFound:
        npx = shutil.which("npx")
        if (
            npx
            and await wait_exec(npx, pyright, "--version", raise_on_error=False) == 0
        ):
            cmd = [npx, pyright]
        else:
            raise

    await wait_exec(
        *cmd,
        "--pythonpath",
        python,
        "oh_so_smart",
        *glob(os.path.join("scripts", "*.py")),
    )


async def _run_shellcheck():
    shellcheck = _check_which(
        "shellcheck",
        install_instructions="Installation instructions:\n"
        "https://github.com/koalaman/shellcheck?tab=readme-ov-file#installing",
    )
    await wait_exec(shellcheck, "--version")
    await wait_exec(
        shellcheck,
        *glob(os.path.join("scripts", "*.sh")),
    )


async def _run_check_uncommitted():
    git = _check_which(
        "git",
        install_instructions="Installation instructions:\n"
        "https://git-scm.com/downloads",
    )
    await wait_exec(git, "--version")
    out, _err, _code = await communicate(git, "status", "--porcelain")
    out = out.decode()
    if out.strip():
        msg = textwrap.dedent(
            """\
            ‘git status’ reports uncommitted changes:
            {}
            This is usually caused by ‘black’ reformatting the source code.
            Please run ./scripts/lint.py on your machine and commit any changes."""
        ).format(out.rstrip())
        raise ValidationError(msg)


async def _run_subcommand(opts: Opts):
    os.chdir(DEFAULT_PROJECT_DIR)

    tools = opts.tools
    for tool in tools:
        _print_header(tool)
        try:
            await globals()[f"_run_{tool.replace('-', '_')}"]()
            _logger.info("\n✅ PASS")
        except Exception as e:
            _logger.info("\n❌ FAIL")
            _logger.error("FAILED to run ‘%s’ tool.", tool)
            raise e


def _parse_cmd_line():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """\
            Run selected code formatter and linter tools.

            ‘ruff check’, ‘ruff format’, ‘pylint’ and ‘pyright’ are executed on Python
            source files. ‘shellcheck’ is executed on shell scripts.

            ‘check-uncommitted’ is a custom operation that runs ‘git status’ to check for
            any uncommitted (modified) files resulting from running code formatters. If
            uncommitted files are found, this script exits with an non-zero status code.
            """
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "tools",
        metavar="tool",
        action="extend",
        nargs="*",
        help=textwrap.dedent(
            """\
            A space-separated list of tool names from the following list:

            {}

            If no arguments are provided, the default is to run all tools except
            for ‘check-uncommitted’. ‘all’ is shorthand for all tools, including
            ‘check-uncommitted’. Tools are run in the order listed above, even
            if they are provided in a different order on the command line.""".format(
                " ".join(AVAILABLE_TOOLS)
            ),
        ),
    )
    return Opts(**vars(parser.parse_args()))


# pylint: disable=duplicate-code
async def main():
    configure_logging(logger=_logger, subproc_log=_logger.info)
    exit_code = 0
    try:
        opts = _parse_cmd_line()
        await _run_subcommand(opts)
    except Exception as e:  # pylint: disable=broad-exception-caught
        _logger.error(e)
        if isinstance(e, SubprocessError):
            exit_code = e.exit_code

        exit_code = exit_code or 1

        if not isinstance(
            e, (ToolNotFound, CommandLineError, SubprocessError, ValidationError)
        ):
            import traceback

            traceback.print_exc()

    if exit_code:
        _logger.error("Exiting with error code %s", exit_code)
        sys.exit(exit_code)


if __name__ == "__main__":
    check_python_version(3, 11, print_and_exit=True)
    asyncio.run(main())
