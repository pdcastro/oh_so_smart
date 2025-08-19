#!/usr/bin/env python3
"""Upload project files from a workstation to the target device.

See additional documentation with ‘upload.py --help’ or in the _parse_cmd_line()
function in this file.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import ntpath
import os
import posixpath
import sys
import textwrap
from dataclasses import dataclass
from os.path import abspath, join, normpath
from shlex import quote
from shutil import which

from common import (
    CommandLineError,
    SubprocessError,
    add_common_options,
    check_python_version,
    communicate,
    configure_logging,
    max_gather,
    set_logging_level,
    wait_exec,
)
from config import (
    APP_NAME,
    DEFAULT_PROJECT_DIR,
    APP_CONTAINER_PROJECT_DIR,
    Config,
    FlatConfig,
)

_logger = logging.getLogger(__name__)


@dataclass
class Opts:
    config_file: str  # Absolute or relative path to the current working dir
    all: bool
    container: bool
    delete: bool
    local_project_dir: str  # Always an absolute path
    log_level: int  # The numeric log level value of e.g. logging.INFO


def _slashed(path: str, sep=os.sep) -> str:
    """Append ‘sep’ to ‘path’ if it does not already end with ‘/’ or ‘\\’."""
    return path if (not path or path[-1] in (ntpath.sep, posixpath.sep)) else path + sep


def _posijoin(root: str, *paths: str) -> str:
    """Posix-join ‘root’ and ‘path’, then posix-normalise and quote the result."""
    return quote(
        posixpath.normpath(
            posixpath.join(root, *paths).replace(ntpath.sep, posixpath.sep)
        )
    )


def _check_rsync():
    if which("rsync"):
        return

    msg = ["Command not found: rsync"]
    # Note: macOS ships with rsync.
    if sys.platform == "linux":
        msg.append(
            'Install rsync, e.g. "sudo apt install rsync" or "sudo dnf install rsync"'
        )
    elif sys.platform == "win32":
        msg.append(
            "Install rsync in a Linux distro with the Windows Subsystem for Linux:\n"
            "https://learn.microsoft.com/en-us/windows/wsl/install"
        )
    raise FileNotFoundError("\n".join(msg))


def _filter_walk(
    root: str, rel_to: str, exclude_dirs: set[str], exclude_files: set[str]
) -> dict[str, list[str]]:
    """‘os.walk(root)’ filtering out selected directories and files.

    Args:
        root (str): Root directory for ‘os.walk(root)’.
        rel_to (str): Directory to which paths are made relative in the returned
            dict. Must be a prefix (substring at the beginning) of ‘root’.
        exclude_dirs (set[str]): Set of directory names to exclude from ‘os.walk()’.
        exclude_files (set[str]): Set of file names to exclude from ‘os.walk()’.

    Raises:
        Exception: If ‘rel_to’ is not a parent directory or the same directory
        as ‘root’.

    Returns:
        dict[str, list[str]]: Keys are the visited directory paths relative to
        ‘rel_to’, and values are the relative paths of each file in that directory.
    """
    root = _slashed(normpath(root))
    rel_to = _slashed(normpath(rel_to))
    if not root.startswith(rel_to):
        raise Exception(
            f"{_filter_walk.__name__}: ‘root’ must start with ‘rel_to’:\n"
            f"root: ‘{root}’\n"
            f"rel_to: ‘{rel_to}’"
        )

    result: dict[str, list[str]] = {}
    rel_len = len(rel_to)
    for directory, directories, files in os.walk(root):
        directories[:] = set(directories) - exclude_dirs
        files[:] = set(files) - exclude_files
        rel_dir = directory[rel_len:]
        result[rel_dir] = [join(rel_dir, f) for f in files]

    return result


# Check whether the app container is running on the target device
async def _is_container_running(cfg: FlatConfig):
    remote_cmd = (
        '"$(command -v docker || command -v podman || command -v balena-engine || echo docker)"'
        " container inspect --format '{{.State.Running}}' "
        f"{cfg.deployment_container_name}"
    )
    args = [which("ssh") or "ssh", cfg.deployment_ssh_host_name, remote_cmd]
    out, err, code = await communicate(*args, raise_on_error=False)
    out = out.strip().lower()
    is_running = code == 0 and out == b"true"
    _logger.debug(
        "is_container_running('%s') is %s (code=%d stdout=%s stderr=%s)",
        cfg.deployment_container_name,
        is_running,
        code,
        out,
        err,
    )
    return is_running


async def _rsync_to_container(opts: Opts, cfg: FlatConfig):
    """Upload project files to a running Docker container."""

    exclude = ".DS_Store .git data env venv img unused __pycache__ \
        *.pyc .mypy_cache .python-version .vscode site-packages"
    rsh_cmd = (
        f"'{quote(sys.executable)}' '{quote(abspath(__file__))}' "
        f"--rsh '{quote(cfg.deployment_container_name)}'"
    )
    await wait_exec(
        which("rsync") or "rsync",
        "-rlptDv",
        "--mkpath",
        *(["--delete"] if opts.delete else []),
        *(f"--exclude={pattern}" for pattern in exclude.split()),
        "--rsh",
        rsh_cmd,
        _slashed(opts.local_project_dir),
        _slashed(f"{cfg.deployment_ssh_host_name}:{APP_CONTAINER_PROJECT_DIR}"),
    )


async def _scp_to_host_os(opts: Opts, cfg: FlatConfig):
    """Transfer selected files to the target device’s host OS project directory.

    If ‘opts.all’ is set, all project files are transferred from the development
    workstation to the target device. Otherwise, only the following script files
    are transferred:
        - The TOML configuration file.
        - ‘scripts/docker.py’
        - ‘scripts/docker.sh’ (in case the host OS does not have Python installed)
        - ‘scripts/common.py’ (used by ‘docker.py’)
        - ‘scripts/config.py’ (used by ‘docker.py’)

    These are the only files needed to start the application container, assuming
    that the Docker image was previously transferred from the workstation to the
    target device with the ‘docker.py save’ script.

    The implementation would have been simpler and shorter using ‘rsync’ instead
    of ‘scp’, however we assume that the target device’s host OS may be minimal
    and immutable such as an off-the-shelf Ubuntu Core distro that does not have
    ‘rsync’. Also, Windows workstations don’t have rsync, unless using WSL.
    """
    from collections.abc import Coroutine
    from itertools import groupby
    from typing import Any

    @dataclass
    class Transfer:
        # Source files. Paths are absolute or relative to ‘opts.local_project_dir’.
        sources: list[str]
        # Destination directory relative to ‘cfg.deployment_host_os_project_dir’.
        dest: str
        mode: str  # chmod mode spec

    transfers = [
        Transfer(sources=[abspath(opts.config_file)], dest="config", mode="0660")
    ]

    def walk_src_dir(src_dir: str, mode: str):
        for directory, files in _filter_walk(
            join(opts.local_project_dir, src_dir),
            rel_to=opts.local_project_dir,
            exclude_dirs={"__pycache__", "manage-packages"},
            exclude_files={"release.py", "manage-packages.mjs"},
        ).items():
            transfers.append(Transfer(sources=files, dest=directory, mode=mode))

    if opts.all:
        # Source code under the ‘oh_so_smart’ and ‘scripts’ folders
        walk_src_dir("oh_so_smart", "0664")
        walk_src_dir("scripts", "0775")
        transfers.append(
            Transfer(
                sources=[
                    ".dockerignore",
                    "Dockerfile.alpine",
                    "Dockerfile.debian",
                    "pyproject.toml",
                    "requirements.txt",
                ],
                dest="",  # Relative to ‘cfg.deployment_host_os_project_dir’
                mode="0664",
            )
        )
    else:
        # Only the script files needed to start the application container.
        transfers.append(
            Transfer(
                sources=[
                    join("scripts", f)
                    for f in ["common.py", "config.py", "docker.py", "docker.sh"]
                ],
                dest="scripts",
                mode="0775",
            )
        )
    # Gather all destination directories as arguments for ‘mkdir -p’
    host_dir = cfg.deployment_host_os_project_dir
    dest_dirs = " ".join(sorted(set(_posijoin(host_dir, tr.dest) for tr in transfers)))
    await wait_exec(
        which("ssh") or "ssh", cfg.deployment_ssh_host_name, f"mkdir -p {dest_dirs}"
    )
    scp_opts = "-r"
    is_unix = os.name == "posix"  # Includes Linux and macOS, of particular interest.
    if is_unix:
        scp_opts += "p"  # Preserve file mode. Unsuitable on Windows filesystems.
    if opts.log_level > logging.DEBUG:
        scp_opts += "q"  # Supress file transfer progress report

    tasks: list[Coroutine[Any, Any, int]] = [
        wait_exec(
            which("scp") or "scp",
            scp_opts,
            *(join(opts.local_project_dir, s) for s in tr.sources),
            f"{cfg.deployment_ssh_host_name}:{_posijoin(host_dir, tr.dest)}",
        )
        for tr in transfers
    ]
    await max_gather(4, *tasks)

    if not is_unix:
        # Fix file permissions on the target device when uploading
        # from a Windows workstation (excluding Cygwin and WSL).
        tasks.clear()
        for mode, group in groupby(transfers, lambda tr: tr.mode):
            if dest := next(group).dest:
                tasks.append(
                    wait_exec(
                        which("ssh") or "ssh",
                        cfg.deployment_ssh_host_name,
                        f"find {_posijoin(host_dir, dest)} "
                        f"-type f -execdir chmod {mode} '{{}}' ';'",
                    )
                )
        await max_gather(2, *tasks)


async def _rsync_rsh():
    # argv[0]  = Path of this Python script
    # argv[1]  = "--rsh"
    # argv[2]  = application container name
    # argv[3]  = target device hostname (given by the ‘rsync’ process)
    # argv[4:] = rsync’s remote command, e.g. ['rsync', '--server',
    #   '-abcdefghij.klmnopqrs', '--mkpath', '.', '/usr/src/app/']
    _logger.debug("%s: sys.argv: %s", _rsync_rsh.__name__, sys.argv)
    if len(sys.argv) < 5:
        raise CommandLineError("--rsh option used but len(sys.argv) < 5")
    container_name = quote(sys.argv[2])
    hostname = sys.argv[3]
    rsync_cmd = " ".join(quote(i) for i in sys.argv[4:])
    remote_cmd = (
        '"$(command -v docker || command -v podman || command -v balena-engine || echo docker)"'
        f" exec -i {container_name} {rsync_cmd}"
    )
    await wait_exec(which("ssh") or "ssh", hostname, remote_cmd)


async def _run_operation(opts: Opts):
    cfg = FlatConfig.from_config(Config(opts.config_file))

    if opts.container:
        _check_rsync()
        if await _is_container_running(cfg):
            _logger.info(
                "rsync’ing to running container ‘%s’ on ‘%s’",
                cfg.deployment_container_name,
                cfg.deployment_ssh_host_name,
            )
            await _rsync_to_container(opts, cfg)
        else:
            _logger.error(
                "rsync to ‘%s’: container not found or not running",
                cfg.deployment_container_name,
            )
    else:
        await _scp_to_host_os(opts, cfg)


def _parse_cmd_line() -> Opts:
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """
            Upload project files from a workstation to the target device.

            The target device is the device that runs the {app_name} application,
            for example a Raspberry Pi.

            Some settings are derived from the TOML configuration file specified
            with the ‘-c’ flag, including the target device’s ssh host name and
            application container name (which is the same as the product ‘slug’
            setting).

            If the ‘--container’ option is used, the application project files are
            uploaded to the ‘{container_dir}’ directory of a running Docker container
            on the target device. Otherwise, files are uploaded to the target
            device’s host OS directory specified in the TOML configuration file
            ‘host_os_project_dir’ setting. In the latter case, the ‘--all’ option
            selects whether all project files are transferred, or only the few
            script files required to start the application container.

            Note that this script only uploads project files, not a Docker image.
            The ‘save’ subcommand of the ‘docker.py’ or ‘docker.sh’ scripts (in
            the same folder as this script) can be used to upload a Docker image.

            This script uses the ‘rsync’, ‘ssh’ and ‘scp’ tools. It assumes that
            ssh access to the device’s host OS (not a container) is configured
            with public key authentication (no password prompts), with the
            username and port number specified in the ‘~/.ssh/config’ file,
            for example with the following ‘Host’ configuration section:

            Host pi4
                Hostname 192.168.1.123
                User root
                Port 22
                PreferredAuthentications publickey
                ConnectTimeout 5

            Where ‘pi4’ should match the ‘ssh_host_name’ setting in the TOML
            configuration file. With the above config, ‘ssh pi4’ opens a shell
            on the host OS without password prompts and without the need of
            specifying a username and port number on the command line. This may
            be relevant because this script uses the ‘rsync --rsh’ option to
            upload files to a running Docker container, leveraging the ‘docker
            exec’ command executed on the host OS. (The ‘rsync’ tool needs to be
            installed in the application container, but the container does not
            need to have an ‘ssh’ client or server installed.)
            """.format(
                app_name=APP_NAME,
                container_dir=APP_CONTAINER_PROJECT_DIR,
            )
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    add_common_options(parser, ["config"])

    parser.add_argument(
        "--all",
        action="store_true",
        help=textwrap.dedent(
            """\
            Transfer all project files, rather than only the files required to
            run a Docker application container out of a pre-built Docker image.
            Transferring all files enables two alternative deployment scenarios:
            - Running the application directly on the target device’s host OS,
              without using Docker containers.
            - Building an application Docker image on the target device’s host OS
              (rather than building the image on a development workstation and
              then uploading it to the target device’s host OS).
            """
        ),
    )
    parser.add_argument(
        "--container",
        action="store_true",
        help=textwrap.dedent(
            """\
            Upload the project files to the ‘{container_dir}’ directory of a
            running application Docker container on the target device, rather
            than to the target device’s host OS. Implies ‘--all’.
            """.format(container_dir=APP_CONTAINER_PROJECT_DIR)
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help=textwrap.dedent(
            """\
            Pass the ‘--delete’ option to ‘rsync’. Use with care! Double check
            that ‘rsync’ is operating as intended before you use this option.
            Mistaken configuration may result in unexpected file deletion.
            """
        ),
    )
    parser.add_argument(
        "--local-project-dir",
        metavar="DIR",
        default=DEFAULT_PROJECT_DIR,
        help=textwrap.dedent(
            """\
            Local project directory where the GitHub repository was cloned.
            Used as the source directory for ‘scp’ and ‘rsync’. Assumed to be
            this script’s parent directory by default (‘{}’).""".format(
                DEFAULT_PROJECT_DIR
            )
        ),
    )

    add_common_options(parser, ["log_level"])

    parsed = parser.parse_args()
    opts = Opts(
        **{**vars(parsed), "log_level": set_logging_level(parsed.log_level, _logger)}
    )
    opts.local_project_dir = abspath(opts.local_project_dir)

    return opts


# pylint: disable=duplicate-code
async def main():
    configure_logging(logger=_logger, subproc_log=_logger.info)
    exit_code = 0
    try:
        # Skip argparse parsing for --rsh because rsync appends arbitrary
        # arguments to the command line, e.g. ‘--server’, and argparse’s
        # ‘parse_known_args()’ function may consume arguments mistakenly
        # believed to be ‘known’.
        if len(sys.argv) > 1 and sys.argv[1] == "--rsh":
            return await _rsync_rsh()

        opts = _parse_cmd_line()
        await _run_operation(opts)
    except (Exception, asyncio.CancelledError, KeyboardInterrupt) as e:
        _logger.error(e)
        if isinstance(e, SubprocessError):
            exit_code = e.exit_code  # pylint: disable=no-member

        exit_code = exit_code or 1

        expected_exc = (
            asyncio.CancelledError,
            CommandLineError,
            FileNotFoundError,
            KeyboardInterrupt,
            SubprocessError,
        )
        if not isinstance(e, expected_exc):
            import traceback

            traceback.print_exc()

    if exit_code:
        _logger.error("Exiting with error code %s", exit_code)
        sys.exit(exit_code)


if __name__ == "__main__":
    # Python v3.6 or later is required for f-strings and type hint syntax.
    # Python v3.10 or later is required for the match statement.
    # Python v3.11 or later is required for the tomllib module.
    check_python_version(3, 11, print_and_exit=True)
    asyncio.run(main())
    _logger.info("All done!")
