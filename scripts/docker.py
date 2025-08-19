#!/usr/bin/env python3
"""Automation script for building Docker images and running containers.

See additional documentation with ‘docker.py --help’ or in the _parse_cmd_line()
function in this file.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import shlex
import sys
import textwrap
from asyncio.subprocess import DEVNULL
from dataclasses import dataclass
from os.path import abspath, basename, exists, join
from shutil import which

from common import (
    CommandLineError,
    SubprocessError,
    ValidationError,
    add_common_options,
    check_python_version,
    communicate,
    configure_logging,
    pipe_subproc,
    set_logging_level,
    wait_exec,
)
from config import (
    APP_CONTAINER_CONFIG_DIR,
    APP_NAME,
    DEFAULT_PROJECT_DIR,
    INDEX_ANNOTATION_DESCRIPTION,
    INDEX_ANNOTATION_LICENCE,
    INDEX_ANNOTATION_SOURCE,
    LOCAL_IMAGE_NAME,
    REGISTRY_IMAGE_NAME,
    Config,
    FlatConfig,
)


_logger = logging.getLogger(__name__)


@dataclass
class HostOSData:
    """Data about the ‘host OS’ (outside the container).

    If the ‘docker.sh’ shell script executes this Python script in a Docker
    container, this dataclass contains data about the host OS environment.
    This data is used by the ‘--print-command-lines’ option, and also used
    for this script to print warnings in case ‘sudo’ should have been used.
    """

    config_file: str = ""  # E.g. ‘/home/bob/oh_so_smart/config/smart_socket.toml’
    image_for_run_command: str = ""  # E.g. ‘oh_so_smart:latest’
    project_dir: str = ""  # E.g. ‘/home/bob/oh_so_smart’
    os_release_id: str = ""  # E.g. ‘ubuntu-core’ (the ‘ID’ field of ‘/etc/os-release’)
    uname_o: str = ""  # Output of ‘uname -o’, e.g. ‘GNU/Linux’ or ‘Darwin’
    user: str = ""  # shell's $USER variable
    # Comma-separated list of usernames for the ‘docker’ group in ‘/etc/group’
    docker_group_users: str = ""


@dataclass
class Opts:
    config_file: str
    docker_cli: list[str]
    docker_platforms: list[str]
    dockerfile_command: list[str]
    host_os_data: HostOSData
    image: str
    log_level: int  # The numeric log level value of e.g. logging.INFO
    print_command_lines: bool
    project_dir: str
    publish: bool
    python_distro: str
    subcommand: str
    sudo: bool


async def _print_or_exec(opts: Opts, *args: str, **kwargs):
    if not opts.print_command_lines:
        return await wait_exec(*args, **kwargs)

    def is_redirect(x: str) -> bool:
        for c in ("<", ">", "2>", "&>", "|"):
            if x.startswith(c):
                return True
        return False

    if (
        os.name == "posix"
        and kwargs.get("stderr") == DEVNULL
        and not any(is_redirect(arg) for arg in args)
    ):
        args = args + ("2>/dev/null",)

    quoted = [arg if is_redirect(arg) else shlex.quote(arg) for arg in args]
    print("+##", " ".join(quoted))


def _validate_build_directories(opts: Opts, cfg: FlatConfig):
    def is_root_dir(path: str) -> bool:
        from pathlib import PurePosixPath, PureWindowsPath

        ppath = PurePosixPath(path)
        wpath = PureWindowsPath(path)
        return (len(ppath.parts) == 1 and ppath.root == "/") or (
            len(wpath.parts) == 1 and wpath.root == "\\"
        )

    # ‘opts.host_os_data.project_dir’ may be a Posix or Windows path.
    proj_dir = opts.host_os_data.project_dir or opts.project_dir
    if is_root_dir(proj_dir):
        raise ValidationError(
            f"""
The project directory ‘{proj_dir}’ must not be the root directory because it is
used as the Docker engine’s “build context” and it is copied to the built Docker
image. The project directory is specified through the ‘--project-dir’ option and
by default it is assumed to be this script’s parent directory."""
        )

    # A Dockerfile must be found in the project directory.
    if not all(
        exists(join(opts.project_dir, f))
        for f in ["Dockerfile.alpine", "Dockerfile.debian"]
    ):
        raise ValidationError(
            """
Neither ‘Dockerfile.alpine’ nor ‘Dockerfile.debian’ were found in the project
directory. Hint: If you are using the ‘upload.py’ script, add the ‘--all’ option
in order to upload all project files."""
        )

    if cfg.deployment_python_distro not in ["alpine", "debian"]:
        raise ValidationError(
            "Config file attribute ‘deployment.python_distro’: expected "
            f"‘alpine’ or ‘debian’, got ‘{cfg.deployment_python_distro}’."
        )


async def _docker_build(opts: Opts, cfg: FlatConfig):
    _validate_build_directories(opts, cfg)

    if opts.print_command_lines and opts.host_os_data.project_dir:
        proj_dir = opts.host_os_data.project_dir
    else:
        proj_dir = opts.project_dir

    dockerfile = f"Dockerfile.{cfg.deployment_python_distro}"
    dockerfile_path = join(proj_dir, dockerfile)
    # If executed in a GitHub Actions runner (CI = ‘Continuous Integration’),
    # use ‘buildx build’ and push the image to the GitHub Container Registry.
    if os.environ.get("CI"):
        out_type = "registry" if opts.publish else "image"
        build_cmd = [
            "buildx",
            "build",
            f"--output=type={out_type}",
            # After pushed to the registry, these annotations can be inspected with:
            # ‘docker buildx imagetools inspect --raw ghcr.io/pdcastro/oh_so_smart’
            f"--annotation=index:org.opencontainers.image.description={INDEX_ANNOTATION_DESCRIPTION}",
            f"--annotation=index:org.opencontainers.image.source={INDEX_ANNOTATION_SOURCE}",
            f"--annotation=index:org.opencontainers.image.licenses={INDEX_ANNOTATION_LICENCE}",
        ]
    else:
        build_cmd = ["build"]

    cmd = [
        *opts.docker_cli,
        *build_cmd,
        f"--file={dockerfile_path}",
        f"--platform={','.join(f'linux/{plat}' for plat in cfg.deployment_docker_platforms)}",
        "--progress=plain",
        "--pull",
        f"--tag={opts.image}",
        proj_dir,
    ]
    await _print_or_exec(opts, *cmd)


async def _docker_save(opts: Opts, cfg: FlatConfig):
    """Save (upload) a Docker image from the workstation to the target device.

    The idea is to run ‘docker save’ on the workstation and ‘docker load’
    on the target device, piping the former’s stdout to the latter’s stdin
    over ssh.

    Assumptions:
    - The Docker image was previosly built on the workstation with the
      ‘docker.py build’ command.
    - ‘ssh’ is configured on the workstation with public key authentication,
      such that ‘ssh <hostname>’ successfully opens a command prompt on the
      target device without prompting the user to type a password. <hostname>
      is the hostname of the target device, saved in the TOML configuration
      file ‘deployment.hostname’ attribute.
    """
    platforms = opts.docker_platforms or cfg.deployment_docker_platforms
    if len(platforms) > 1:
        source = "command line" if opts.docker_platforms else "config file"
        raise ValidationError(
            f"The {source} specifies multiple Docker platforms, but only one platform "
            "can be saved with the ‘save’ subcommand. Please use the ‘--docker-platforms’ "
            "option to specify a single platform."
        )
    plat_opt = ["--platform", f"linux/{platforms[0]}"] if platforms else []
    save_cmd = [*opts.docker_cli, "save", *plat_opt, opts.image]
    remote_cmd = [
        *(["sudo"] if opts.sudo else []),
        '"$(command -v docker || command -v podman || command -v balena-engine || echo docker)"',
        "load",
    ]
    ssh_cmd = [which("ssh") or "ssh", cfg.deployment_ssh_host_name, *remote_cmd]

    if opts.print_command_lines:
        await _print_or_exec(opts, *save_cmd, "|", *ssh_cmd)
        return

    docker_code, ssh_code = await pipe_subproc(save_cmd, ssh_cmd)
    if docker_code or ssh_code:
        _logger.error(
            "%s: Operation failed with docker exit code %d and ssh exit code %d.",
            _docker_save.__name__,
            docker_code,
            ssh_code,
        )
    else:
        _logger.info("Docker image transferred successfully 🎉")


async def _docker_run(opts: Opts, cfg: FlatConfig):
    from glob import glob
    from itertools import chain

    cmd = [*opts.docker_cli, "kill", "-s", "15", cfg.deployment_container_name]
    await _print_or_exec(opts, *cmd, stderr=DEVNULL, raise_on_error=False)

    cmd = [*opts.docker_cli, "rm", "-f", cfg.deployment_container_name]
    await _print_or_exec(opts, *cmd, stderr=DEVNULL, raise_on_error=False)

    container_cfg_path = f"{APP_CONTAINER_CONFIG_DIR}/{cfg.config_file_basename}"
    host_cfg_path = opts.host_os_data.config_file or abspath(cfg.config_file_path)

    if opts.dockerfile_command:
        # Interactive usage, e.g.: ‘docker.py run bash’
        # Replace the ENTRYPOINT defined in the Dockerfile
        daemon_opts = ["--interactive", "--tty", "--rm", "--restart=no"]
        entrypoint = ["--entrypoint=/usr/bin/env"]
        dockerfile_cmd = opts.dockerfile_command
    else:
        # Daemon usage: Complement the ENTRYPOINT defined in the Dockerfile
        daemon_opts = ["--detach", "--restart=unless-stopped"]
        entrypoint = []
        dockerfile_cmd = [f"--config-file={container_cfg_path}"]

    bind_opts = [f"--volume={host_cfg_path}:{container_cfg_path}"]
    device_opts = [f"--device={dev}" for dev in glob("/dev/gpiochip*")]
    security_opts = ["--security-opt", "label=disable"]  # Fedora IoT SELinux
    env_opts = chain.from_iterable(
        ("-e", var)
        for var in (
            "DEBUG",
            "MQTT_SERVER_HOSTNAME",
            "MQTT_SERVER_PORT",
            "MQTT_SERVER_USERNAME",
            "MQTT_SERVER_PASSWORD",
        )
    )
    cmd = [
        *opts.docker_cli,
        "run",
        *daemon_opts,
        "--init",
        *bind_opts,
        *device_opts,
        *security_opts,
        *env_opts,
        *entrypoint,
        "--name",
        cfg.deployment_container_name,
        opts.image,
        *dockerfile_cmd,
    ]
    await _print_or_exec(opts, *cmd)

    if not opts.print_command_lines and not opts.dockerfile_command:
        _logger.info(
            """
Container ‘%s’ started. You can check the logs with the
‘%s logs -f %s’ command line.""",
            cfg.deployment_container_name,
            " ".join(basename(c) for c in opts.docker_cli),
            cfg.deployment_container_name,
        )


async def _run_subcommand(opts: Opts):
    plats, distro, cmd = opts.docker_platforms, opts.python_distro, opts.subcommand
    cfg = FlatConfig.from_config(Config(opts.config_file))
    cfg.deployment_python_distro = distro or cfg.deployment_python_distro
    cfg.deployment_docker_platforms = plats or cfg.deployment_docker_platforms

    if cmd == "build":
        await _docker_build(opts, cfg)
    elif cmd == "save":
        await _docker_save(opts, cfg)
    elif cmd == "run":
        await _docker_run(opts, cfg)


def _warn_about_sudo(host_os_data: HostOSData):
    if "Linux" not in (host_os_data.uname_o or platform.system()):
        return

    if host_os_data.user:
        user = host_os_data.user
        docker_group_users = host_os_data.docker_group_users.split(",")
    else:
        from getpass import getuser
        from grp import getgrnam

        user = getuser()
        try:
            docker_group_users = getgrnam("docker").gr_mem
        except KeyError:
            docker_group_users = []

    if user and user not in ["root", "runner", *docker_group_users]:
        _logger.warning(
            """
This script was executed by non-root user ‘%(user)s’ and it appears that Docker
was not configured for execution by non-priviledged users. Possible solutions:
- Run this script under the ‘root’ user account
- Run this script with ‘sudo’, e.g. ‘sudo docker.sh ...’
- Configure Docker for execution by non-privileged users:
  https://docs.docker.com/engine/install/linux-postinstall/
""",
            {"user": user},
        )


def _parse_host_os_data(host_os_data: list[str] | None) -> HostOSData:
    if not host_os_data:
        return HostOSData()

    if len(host_os_data) % 2:
        raise CommandLineError(
            f"Odd number of arguments for the ‘-d’ option ({len(host_os_data)}). "
            "It must be an even number of space-separated key-value pairs."
        )

    from collections.abc import Iterable
    from itertools import batched
    from typing import cast

    data_dict: dict[str, str] = dict(
        cast(Iterable[tuple[str, str]], batched(host_os_data, n=2))
    )
    try:
        return HostOSData(**data_dict)
    except TypeError as e:
        raise CommandLineError(f"Unexpected ‘--host-os-data’ option key: {e}") from e


async def _select_image_name(docker_cmd: list[str]) -> str:
    """Select an image name for the ‘run’ or ‘save’ subcommands.

    Preference is given to a locally built ‘oh_so_smart’ image if present,
    otherwise ‘ghcr.io/pdcastro/oh_so_smart’.
    """
    out, _err, _exit_code = await communicate(
        *docker_cmd, "images", "--format", "{{.Repository}}:{{.Tag}}"
    )
    selected = f"{REGISTRY_IMAGE_NAME}:latest"
    # ‘podman’ lists local images with a ‘localhost/’ prefix.
    local_latest: list[bytes] = [
        f"{LOCAL_IMAGE_NAME}:latest".encode(),
        f"localhost/{LOCAL_IMAGE_NAME}:latest".encode(),
    ]
    # ‘out’ and ‘line’ are of type ‘bytes’ (not ‘str’).
    for line in out.split(b"\n"):
        if line.strip() in local_latest:
            selected = local_latest[0].decode()
            break

    _logger.info("Selected image name ‘%s’", selected)
    return selected


async def _parse_cmd_line() -> Opts:
    parser = argparse.ArgumentParser(
        description="Automate the execution of the ‘docker build’, ‘docker save’ and ‘docker run’ commands.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "subcommand",
        choices=["build", "save", "run"],
        help=textwrap.dedent(
            """\
            Automate the execution of the ‘docker build’, ‘docker save’ and
            ‘docker run’ commands respectively:
            build: Build a Docker image on the local machine, which may be
                   a development workstation (cross build) or the target
                   device (e.g. a Raspberry Pi).
            save:  Upload the Docker image from a workstation to the target
                   device (e.g. a Raspberry Pi).
            run:   Create and start Docker container to run the {}
                   application. This command is normally executed on the
                   target device. If a suitable Python interpreter is not
                   available on the target device’s host OS in order to run
                   this Python script, check the ‘docker.sh’ shell script that
                   runs this Python script in a Docker container.\
            """.format(APP_NAME)
        ),
    )
    parser.add_argument(
        "dockerfile_command",
        metavar="dockerfile-command",
        default="",
        nargs="*",
        help=textwrap.dedent(
            """\
            Optional command and arguments to be executed in the
            application’s container instead of the default entrypoint
            defined in the Dockerfile. Only applies to the ‘run’
            subcommand. For example, use the ‘sh’ command in order to
            open an interactive shell in the application’s container
            that allows you to manually run the app for debugging."""
        ),
    )

    add_common_options(parser, ["config"])

    parser.add_argument(
        "--image",
        metavar="NAME:TAG",
        default="",
        help=textwrap.dedent(
            """\
            Name or name:tag of the Docker image to build, run or save. When
            building, the default is ‘{local_name}:latest’. When running or
            saving, the default is ‘{local_name}:latest’ if such an image
            exists, otherwise ‘{reg_name}:latest’.""".format(
                local_name=LOCAL_IMAGE_NAME, reg_name=REGISTRY_IMAGE_NAME
            )
        ),
    )
    parser.add_argument(
        "--project-dir",
        metavar="DIR",
        default=DEFAULT_PROJECT_DIR,
        help=textwrap.dedent(
            """\
            Directory where the ‘build’ subcommand looks for project files
            (such as the Dockerfiles). This may be the directory where the
            GitHub repository was cloned, or the directory specified in the
            ‘host_os_project_dir’ setting of the TOML configuration file when
            the ‘scripts/upload.py’ script is used. Assumed to be this
            script’s parent directory by default."""
        ),
    )
    parser.add_argument(
        "-p",
        "--print-command-lines",
        action="store_true",
        help=textwrap.dedent(
            """\
            Print Docker command lines to stdout, but do not execute them.
            Useful if this Python script itself runs in a Docker container,
            but the Docker command lines should be executed in the caller’s
            host OS context. Used by the ‘docker.sh’ script."""
        ),
    )
    parser.add_argument(
        "--sudo",
        action="store_true",
        help=textwrap.dedent(
            """\
            Prepend ‘sudo’ to the ‘docker’ command lines printed or executed
            by this script on Linux. However, on some systems this causes
            Docker to freeze. Instead of using this option, try running this
            script with ‘sudo’, e.g. ‘sudo docker.py …’ or ‘sudo docker.sh …’
            """
        ),
    )
    plats = ["arm/v6", "arm/v7", "arm64", "i386", "386", "amd64", "x86_64", "x86-64"]
    parser.add_argument(
        "--docker-platforms",
        action="extend",
        nargs="+",
        choices=plats,
        metavar="PLATFORM",
        help=textwrap.dedent(
            """\
            Override the ‘deployment.docker_platforms’ attribute of the TOML
            configuration file. Used by CI builders."""
        ),
    )
    parser.add_argument(
        "--python-distro",
        choices=["alpine", "debian"],
        default="",
        help=textwrap.dedent(
            """\
            Override the ‘deployment.python_distro’ attribute of the TOML
            configuration file. Used by CI builders."""
        ),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=textwrap.dedent(
            """\
            After building a Docker image, publish it to the registry.
            Used by CI builders."""
        ),
    )
    parser.add_argument(
        "--host-os-data",
        action="extend",
        nargs="+",
        metavar="KEY_OR_VALUE",
        help=textwrap.dedent(
            """\
            If this Python script is executed in a container by ‘docker.sh’,
            this option provides some information about the ‘host OS’ such
            as file paths to be used by ‘--print-command-lines’ and the
            username so that this script can print warnings about the use
            of ‘sudo’. The format is a space-separated sequence of key and
            value pairs."""
        ),
    )

    add_common_options(parser, ["log_level"])

    parsed = parser.parse_args()
    opts = Opts(
        **{
            **vars(parsed),
            "docker_cli": [],
            "host_os_data": _parse_host_os_data(parsed.host_os_data),
            "log_level": set_logging_level(parsed.log_level, _logger),
        }
    )
    if opts.dockerfile_command and opts.subcommand != "run":
        raise CommandLineError(
            "A dockerfile command can only be used with the ‘run’ subcommand."
        )
    opts.sudo = opts.sudo and platform.system() == "Linux"
    docker_exe = (
        "docker"
        if opts.print_command_lines
        else (which("docker") or which("podman") or which("balena-engine") or "")
    )
    if not docker_exe:
        raise Exception(
            "Neither ‘docker’ nor ‘balena-engine’ found in the executable PATH"
        )
    opts.docker_cli = ["sudo", "-E", "--", docker_exe] if opts.sudo else [docker_exe]

    if not opts.image:
        run_img = opts.host_os_data.image_for_run_command
        if opts.subcommand == "build":
            opts.image = LOCAL_IMAGE_NAME
        elif opts.print_command_lines:
            opts.image = run_img or REGISTRY_IMAGE_NAME
        else:
            opts.image = run_img or await _select_image_name(opts.docker_cli)

    return opts


# pylint: disable=duplicate-code
async def main():
    configure_logging(logger=_logger, subproc_log=_logger.info)
    exit_code = 0
    try:
        opts = await _parse_cmd_line()
        _warn_about_sudo(opts.host_os_data)
        await _run_subcommand(opts)
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
            ValidationError,
        )
        if not isinstance(e, expected_exc):
            import traceback

            traceback.print_exc()

    if exit_code:
        _logger.error("Exiting with error code %s", exit_code)
        sys.exit(exit_code)


if __name__ == "__main__":
    # Python v3.6 or later is required for f-strings, asyncio and type hints.
    # Python v3.11 or later is required for the tomllib module.
    check_python_version(3, 11, print_and_exit=True)
    asyncio.run(main())
