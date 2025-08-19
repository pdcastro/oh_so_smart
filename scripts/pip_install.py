#!/usr/bin/env python3
"""Automate the pip-installation of gpiod (and other packages) on macOS and Windows.

See additional documentation with ‘pip_install.py --help’ or in the _parse_cmd_line()
function in this file.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import argparse
import asyncio
import logging
import os
import sys
import textwrap
from asyncio.subprocess import DEVNULL
from functools import lru_cache
from os.path import abspath, dirname, isdir, join, normpath

from common import check_python_version, configure_logging, wait_exec

_logger = logging.getLogger(__name__)


def delete_incompatible_binaries(site_pkg_dir: str):
    """If not on Linux, delete incompatible '*.so' binary extension files."""
    if sys.platform.startswith("linux"):
        return

    from glob import glob
    from os import unlink

    files = glob(f"{site_pkg_dir}/gpiod/*.so")
    if 0 < len(files) < 3:
        for file in files:
            _logger.info("Deleting incompatible binary extension file '%s'", file)
            unlink(file)


async def copy_gpiod_from_docker_container(gpiod_pkg_spec: str):
    """Run "pip install gpiod" in a temp Linux container and copy the install dir.

    Args:
        gpiod_pkg_spec (str): Package spec from requirements.txt, e.g. 'gpiod~=2.2'
    """

    v = sys.version_info
    ver2 = f"{v.major}.{v.minor}"  # E.g. '3.12'
    ver3 = f"{v.major}.{v.minor}.{v.micro}"  # E.g. '3.12.4'
    docker_img = f"python:{ver3}-bookworm"
    site_pkg_dir = join(get_venv_dir(), *["lib", f"python{ver2}", "site-packages"])
    bind_dir = "/out-site-packages"
    # fmt: off
    cmd = [
        "docker", "run", "--rm", "-v", f"{site_pkg_dir}:{bind_dir}",
        "--pull", "always", docker_img, "sh", "-c",
        f"pip install '{gpiod_pkg_spec}' 1>&2 && cp -av "
        f"'/usr/local/lib/python{ver2}/site-packages/'gpiod* '{bind_dir}/'",
    ]
    # fmt: on
    await wait_exec(*cmd, stdin=DEVNULL)
    delete_incompatible_binaries(site_pkg_dir)


def filter_requirements() -> tuple[list[str], str]:
    packages = []
    gpiod_pkg = ""
    req_file = join(get_project_dir(), "requirements.txt")

    with open(req_file, "rt", encoding="utf-8") as req:
        lines = req.readlines()

    for line in lines:
        pkg = line.strip()
        if not pkg or pkg.startswith("--"):
            continue
        if pkg.startswith("gpiod"):
            gpiod_pkg = pkg
        else:
            packages.append(pkg)

    return packages, gpiod_pkg


async def pip_install_local(packages: list[str]):
    """pip install -U -r <pkg1, pkg2...> -r requirements_dev.txt"""
    venv_dir = get_venv_dir()
    project_dir = get_project_dir()
    previous_dir = os.getcwd()
    os.chdir(project_dir)
    try:
        await wait_exec(
            join(".", f"{venv_dir}", "bin", "pip"),
            "install",
            "-U",
            *packages,
            "-r",
            join(get_project_dir(), "requirements_dev.txt"),
            stdin=DEVNULL,
        )
    finally:
        os.chdir(previous_dir)


@lru_cache
def get_project_dir():
    return normpath(join(dirname(abspath(__file__)), ".."))


@lru_cache
def get_venv_dir():
    proj_dir = get_project_dir()
    candidates = [join(proj_dir, env_dir) for env_dir in ("venv", "env")]
    for venv_dir in candidates:
        if isdir(venv_dir):
            return venv_dir
    return candidates[0]


async def ensure_venv():
    venv_dir = get_venv_dir()
    if not isdir(venv_dir):
        _logger.warning(
            "Creating a new Python virtual environment at '%s'.",
            venv_dir,
        )
        await wait_exec(sys.executable, "--version")
        await wait_exec(sys.executable, "-m", "venv", venv_dir)


def _parse_cmd_line():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """
            Automate the pip-installation of gpiod (and other packages) on macOS and Windows.

            The ‘gpiod’ Python package provides access to the General Purpose Input/Output pins
            of devices like the Raspberry Pi, through a Linux kernel interface. Given that the
            ‘gpiod’ package only works on Linux, pre-built Python wheels are only available for
            Linux. As a result, “pip install gpiod” fails on macOS and Windows when pip attempts
            to build a wheel (the wheel compilation requires Linux kernel header files).

            However, even if gpiod cannot function on macOS and Windows, it is still useful to
            have it pip-installed on those platforms so that IDEs can provide intellisense
            features such as autocompletion, and so that linters like pylint and type checkers
            like pyright can do their job fully. To this end, this script performs the following
            steps:

            - Extract the list of packages from ‘requirements.txt’, filtering out the gpiod
              package.
            - Run ‘<venv>/bin/pip install -U <packages> -r requirements_dev.txt’, where:
              - <venv> is a local Python virtual environment directory at the project’s root.
              - <packages> is the list of dependencies other than gpiod.
            - Run ‘pip install gpiod~=X.Y’ in a temporary Linux Docker container, and copy
              the ‘.../site-packages/gpiod/*’ files from the container to the local virtual
              environment (by means of a directory bind mount: ‘docker run -v’ option).
            """
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.parse_args()


# pylint: disable=duplicate-code
async def main():
    configure_logging(logger=_logger, subproc_log=_logger.info)
    exit_code = 0
    try:
        _parse_cmd_line()
        await ensure_venv()
        packages, gpiod_pkg = filter_requirements()
        await pip_install_local(packages)
        print()
        await copy_gpiod_from_docker_container(gpiod_pkg)
    except Exception as e:  # pylint: disable=broad-exception-caught
        _logger.error(e)
        exit_code = 1

    if exit_code:
        _logger.error("Error code %s executing script", exit_code)
        sys.exit(exit_code)


if __name__ == "__main__":
    check_python_version(3, 11, print_and_exit=True)
    asyncio.run(main())
