#!/usr/bin/env python3
"""Orchestrate the creation of a GitHub repository release.

This script is called by a GitHub Actions workflow (.github/workflows/ci.yml)
to build the application Docker images, publish them to the GitHub Container
Registry and create a GitHub repository release.

This script uses the Python Semantic Release tool to interact with the GitHub API:
https://python-semantic-release.readthedocs.io/

The logic implemented here could have been in the workflow YAML file instead,
using a mix of proprietary GitHub expression syntax, bash scripts and JavaScript.
But this is a Python project, so let the release logic be coded in Python!

---
Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
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
from config import APP_NAME, to_slug


GITHUB_EVENT_NAME: Final = os.environ.get("GITHUB_EVENT_NAME", "")
GITHUB_REF: Final = os.environ.get("GITHUB_REF", "")
GITHUB_SHA: Final = os.environ.get("GITHUB_SHA", "")
PSR_VENV_DIR: Final = "psr_env"

_logger = logging.getLogger(__name__)


class NothingToDo(Exception):
    pass


class ReleaseError(Exception):
    pass


@dataclass
class Opts:
    # github_event_name: str
    # github_ref: str
    # github_sha: str
    subcommand: str
    # main_release: bool  # ‘main’ branch release


def _get_base_tag(dockerfile_path: str) -> str:
    """Extract part of the base image tag in the last ‘FROM’ line of the given Dockerfile.

    Returns:
        str: E.g. ‘slim-bookworm’ given ‘FROM python:3.13-slim-bookworm’, or
            ‘alpine3.21’ given ‘FROM python:3.13-alpine3.21’.
    """
    # Match lines such as:
    # FROM python:3.13-bookworm AS build
    # FROM python:3.13-slim-bookworm
    from_pattern = re.compile(
        r"^\s*FROM\s+(\S+\s+)*?(?P<img>\S+)(\s+AS\s+\S+)?\s*$",
        re.ASCII | re.IGNORECASE,
    )
    base_image = ""  # E.g. ‘python:3.13-slim-bookworm’
    with open(dockerfile_path, encoding="utf-8") as f:
        for line in f:
            if m := from_pattern.fullmatch(line):
                # Do not ‘break’ here because we want the last ‘FROM’ line
                base_image = m.groupdict()["img"]

    # E.g. extract ‘slim-bookworm’ from ‘python:3.13-slim-bookworm’
    m = re.fullmatch(r"(?ai)\S+?:[0-9.-]*(?P<tag>\S+)", base_image)
    try:
        return m.groupdict()["tag"]  # pyright: ignore[reportOptionalMemberAccess]
    except (AttributeError, KeyError) as e:
        raise ValidationError(
            f"Unrecognised tag in base image ‘{base_image}’ of ‘{dockerfile_path}’"
        ) from e


def _is_main_branch():
    return GITHUB_REF == "refs/heads/main"


def _get_branch_tag() -> str:
    """Make a Docker image tag component including a branch name and commit SHA.

    Returns:
        str: E.g. 'branch_fix_foo.abcd1234', where:
        - ‘branch_’ is a prefix that may also be ‘vtag_’, ‘PR_’ or ‘dev’.
        - ‘fix_foo’ is the name of the branch, version tag or pull request.
        - ‘abcd1234’ are the first 8 characters of the commit SHA.
    """
    # https://docs.github.com/en/actions/reference/variables-reference
    sha_len = len(GITHUB_SHA)
    if sha_len < 8:
        raise ValidationError(
            f"Two few characters ({sha_len}) in ‘github.sha’ context property"
        )
    if m := re.fullmatch("refs/heads/([^/]+)", GITHUB_REF):
        name = f"branch_{m.group(1)}"
    elif m := re.fullmatch("refs/tags/([^/]+)", GITHUB_REF):
        name = f"vtag_{m.group(1)}"
    elif m := re.fullmatch("refs/pull/([0-9]+)/merge", GITHUB_REF):
        name = f"PR_{m.group(1)}"  # Pull Request
    else:
        name = "dev"

    name = name.translate(str.maketrans(":.-", "___"))
    return f"{name}.{GITHUB_SHA[:8]}"


def _get_image_tags_by_distro(
    version_tag: str,
) -> dict[Literal["alpine", "debian"], str]:
    """Compute Docker image tag names for Alpine and Debian distros.

    Args:
        version_tag: A semver or branch prefix, e.g. '1.0.1' or
            'branch_fix_foo.abcd1234'.

    Returns:
        dict: {"distro": "img_tag"} dictionary, e.g.:
        For a branch named ‘fix_foo’:
          { "alpine": "branch_fix_foo.abcd1234-alpine3.22",
            "debian": "branch_fix_foo.abcd1234-slim-bookworm" }
        For the ‘main’ branch:
          { "alpine": "1.0.1-alpine3.22",
            "debian": "1.0.1-slim-bookworm" }
    """
    cwd = os.getcwd()
    tags_by_distro: dict[Literal["alpine", "debian"], str] = {}
    for distro in ("alpine", "debian"):
        base_tag = _get_base_tag(os.path.join(cwd, f"Dockerfile.{distro}"))
        tags_by_distro[distro] = f"{version_tag}-{base_tag}"

    return tags_by_distro


async def _setup_python_semantic_release():
    """Setup Python Semantic Release in a separate virtual environment.

    https://pypi.org/project/python-semantic-release/
    """
    await wait_exec("python", "-m", "venv", PSR_VENV_DIR)
    await wait_exec(
        f"./{PSR_VENV_DIR}/bin/pip", "install", "-r", "requirements_psr.txt"
    )


def _setup_psr_release_notes_context(image_tags: Iterable[str]):
    # Create a symlink to the default PSR Jinja template files.
    template_dir = "docs/psr-templates"
    link_src = f"../../{PSR_VENV_DIR}/lib/python3.13/site-packages/semantic_release/data/templates/conventional/md"
    link_dst = f"{template_dir}/.default"
    try:
        os.unlink(link_dst)
    except FileNotFoundError:
        pass
    os.symlink(link_src, link_dst, target_is_directory=True)

    # Set variables used by the PSR release Jinja template.
    jinja_vars = {
        "oh_image_name": "ghcr.io/pdcastro/oh_so_smart",
        "oh_image_tags": sorted(image_tags),
    }
    jinja_vars_file = f"{template_dir}/.vars.j2"
    # Write Jinja template assignment statements in the format:
    # {% set oh_image_name = 'ghcr.io/pdcastro/oh_so_smart' %}
    # {% set oh_image_tags = ['1.0.0-alpine3.22', '1.0.0-slim-bookworm'] %}
    with open(jinja_vars_file, "w", encoding="utf-8") as f:
        for name, value in jinja_vars.items():
            f.write(f"{{% set {name} = {repr(value)} %}}\n")


async def _get_next_semantic_release_version() -> str:
    """Invoke semantic-release to determine the next version out of commit messages.

    Raises:
        NothingToDo: If no release is needed, e.g. because the commit messages
            do not include version-bumping prefixes.
        ReleaseError: If semantic-release produces a non-zero exit code or an
            unexpected version string.

    Returns:
        str: The next semantic version, e.g. "1.2.3".
    """

    # Note: Using the ‘semantic-release --strict’ flag causes it to produce a
    # non-zero exit code (alongside a stderr message like “No release will be
    # made, 1.1.3 has already been released!”) if the ‘--print’ flag is used and
    # the next version is the same as the last released version (e.g. because
    # the conventional commit messages don’t include version-bumping verbs).
    async def get_version(print_arg: str) -> tuple[bytes, bytes, int]:
        out, err, exit_code = await communicate(
            "./psr_env/bin/semantic-release",
            "--strict",
            "version",
            print_arg,
            raise_on_error=False,
        )
        return out, err, exit_code

    out, err, exit_code = await get_version("--print")
    semver = out.strip()
    semver_ok = bool(re.match(b"[0-9]+\\.[0-9]+\\.[0-9]+", semver))

    if exit_code and semver_ok:
        # Check whether the non-zero exit_code was only because the next
        # version is equal to the last version
        last_ver, _err, _exit_code = await get_version("--print-last-released")
        if semver == last_ver.strip():
            raise NothingToDo(
                "No release required (commit messages do not bump the semantic version)"
            )
    if exit_code or not semver_ok:
        raise ReleaseError(
            "Python Semantic Release error: "
            f"{semver=} {exit_code=} {err.decode(errors='ignore')}"
        )
    semver_str = semver.decode()
    _logger.info("The next semantic release version is '%s'", semver_str)
    return semver_str


async def _get_version_tag() -> str:
    """Return a version tag, e.g. '1.2.3' or 'branch_fix_foo.abcd1234-alpine3.22'."""
    if _is_main_branch():
        return await _get_next_semantic_release_version()
    else:
        return _get_branch_tag()


async def _delete_images(account: str, img_name: str, img_tags: Iterable[str]):
    """Delete images from the registry (GHCR) for the given img_name and img_tags.

    This function executes the ‘manage-packages.mjs’ script, which also checks
    the registry for orphan images (images whose SHA digest is not listed in any
    tagged image index).

    Args:
        account: GitHub account, e.g. 'pdcastro'.
        img_name: Docker image name without the tag, e.g. 'oh_so_smart'.
        img_tags: Docker image tags, e.g. ["1.0.1-alpine3.21", "slim-bookworm"].
    """
    # Build the Typescript script and execute it.
    await wait_exec("pnpm", "install", cwd="./scripts/manage-packages")
    await wait_exec("node", "--run", "build", cwd="./scripts/manage-packages")
    await wait_exec(
        "./scripts/manage-packages.mjs",
        "delete",
        "--orphans",
        f"{account}/{img_name}",  # e.g. 'pdcastro/oh_so_smart'
        *img_tags,
    )


async def _build_docker_image(
    python_distro: Literal["alpine", "debian"], nametag: str, publish: bool
):
    await wait_exec(
        "./scripts/docker.py",
        *["--config-file", "sample_config/smart_thermostat.toml"],
        *["--docker-platforms", "amd64", "arm64", "arm/v7"],
        *["--python-distro", python_distro],
        *["--image", nametag],
        "build",
        *(["--publish"] if publish else []),
    )


async def _publish_docker_images(version_tag: str):
    registry = "ghcr.io"
    account = "pdcastro"
    img_name = to_slug(APP_NAME)  # 'oh_so_smart'
    img_tags = _get_image_tags_by_distro(version_tag)
    nametags_by_distro = {
        distro: f"{img_name}:{tag}" for distro, tag in img_tags.items()
    }

    # First build all Docker images. If all the builds succeed, then publish
    # them to the registry.
    for distro in ("debian", "alpine"):
        registry_nametag = f"{registry}/{account}/{nametags_by_distro[distro]}"
        _logger.info("Building (publish=False) image '%s'", registry_nametag)
        await _build_docker_image(distro, registry_nametag, publish=False)

    # Avoid leaving orphan images in the registry in the exceptional event that
    # the release process fails (before a GitHub repo release is created) and
    # needs to be re-run. GHCR does not automatically delete images that were
    # previously tagged with the same tags being pushed again. (Orphan images
    # are images whose SHA digest is not listed in any tagged image index.)
    await _delete_images(account, img_name, img_tags.values())

    # Publish the images to the registry. We call _build_docker_image() again,
    # but this time it will be fast as all the layers are cached. The ‘docker.py’
    # script will ultimately invoke ‘docker buildx build --output=type=registry’
    # to immediately publish the image to the registry. This is preferable to the
    # separate steps of ‘docker build’, ‘docker tag’ and ‘docker push’ because
    # the separate steps would not preserve image index annotations that provide
    # an image description to the GitHub Container Registry.
    # Debian first so that its timestamp is older and it gets listed second,
    # after Alpine. Alpine is then tagged "latest".
    for distro in ("debian", "alpine"):
        registry_nametag = f"{registry}/{account}/{nametags_by_distro[distro]}"
        _logger.info("Building (publish=True) image '%s'", registry_nametag)
        await _build_docker_image(distro, registry_nametag, publish=True)
        await asyncio.sleep(1)

    # If all of the above succeeded and the build is for the main branch,
    # additionally tag one of the images as ":latest" and push it too.
    if _is_main_branch():
        # ‘ghcr.io/pdcastro/oh_so_smart:latest’
        latest = f"{registry}/{account}/{img_name}:latest"
        registry_nametag = f"{registry}/{account}/{nametags_by_distro['alpine']}"
        _logger.info("Tagging and pushing image '%s'", latest)
        await wait_exec("docker", "tag", registry_nametag, latest)
        await wait_exec("docker", "push", latest)

    _logger.info("Image building finished")


async def _publish_github_release(version_tag: str):
    """Publish a GitHub release.

    Note that the CI workflow will only run the ‘release’ subcommand
    (this function) for the main branch (GITHUB_REF == "refs/heads/main"),
    so there is no need to check the branch here.

    Args:
        version_tag: A main-branch semver version such as '1.2.3'
    """
    image_tags = _get_image_tags_by_distro(version_tag)
    _setup_psr_release_notes_context(image_tags.values())
    await wait_exec(
        "./psr_env/bin/semantic-release",
        "version",
        "--changelog",
        "--push",
        "--vcs-release",
    )


async def run_subcommand(opts: Opts):
    await _setup_python_semantic_release()
    try:
        version_tag = await _get_version_tag()
    except NothingToDo as e:
        _logger.info("Nothing to do: %s", e)
        return

    if opts.subcommand == "build":
        await _publish_docker_images(version_tag)

    elif opts.subcommand == "release":
        await _publish_github_release(version_tag)


def _parse_cmd_line() -> Opts:
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(
            """
            Create a GitHub repository release and publish Docker images.

            This script uses the Python Semantic Release tool to compute the next release
            version (v-tag) out of commit messages (that follow the Conventional Commits
            standard), update the Changelog file, commit it to the repo and tag the commit.
            The next release version is used to tag Docker images and publish them to the
            GitHub registry.
            """
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        title="subcommands", dest="subcommand", required=True
    )
    _build_parser = subparsers.add_parser(
        "build", description="Build and publish Docker images."
    )
    _release_parser = subparsers.add_parser(
        "release", description="Create a GitHub repository release."
    )
    parsed = parser.parse_args()

    return Opts(**vars(parsed))


# pylint: disable=duplicate-code
async def main():
    configure_logging(logger=_logger, subproc_log=_logger.info)
    exit_code = 0
    try:
        opts = _parse_cmd_line()
        await run_subcommand(opts)

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
    # Python v3.10 or later is required for the match statement.
    # Python v3.11 or later is required for the tomllib module.
    check_python_version(3, 11, print_and_exit=True)
    asyncio.run(main())
