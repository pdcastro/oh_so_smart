"""TOML configuration file parsing code.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from os.path import abspath, basename, dirname, join
from typing import Any, Final

APP_NAME: Final = "Oh So Smart"
LOCAL_IMAGE_NAME: Final = "oh_so_smart"
REGISTRY_IMAGE_NAME: Final = "ghcr.io/pdcastro/oh_so_smart"

APP_CONTAINER_CONFIG_DIR: Final = "/data"
APP_CONTAINER_PROJECT_DIR: Final = "/usr/src/app"
# This script’s parent directory
DEFAULT_PROJECT_DIR: Final = abspath(join(dirname(__file__), ".."))

# Multi-platform image index annotations.
# https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#adding-a-description-to-multi-arch-images
# https://docs.docker.com/reference/cli/docker/buildx/build/#annotation
INDEX_ANNOTATION_LICENCE: Final = "OSL-3.0"
INDEX_ANNOTATION_SOURCE: Final = "https://github.com/pdcastro/oh_so_smart"
INDEX_ANNOTATION_DESCRIPTION: Final = (
    f"{APP_NAME} — An Open Source Smart Device Implementation. "
    "Check the README at https://github.com/pdcastro/oh_so_smart"
)

ConfigT = dict[str, Any]


class ConfigError(Exception):
    pass


def to_slug(name: str) -> str:
    """Convert a name like 'Oh So Smart' to a slug identifier like 'oh_so_smart'."""
    return name.strip().lower().replace(" ", "_")


@dataclass
class FlatConfig:
    """Selected attributes from the TOML configuration file, flattened from subsections."""

    product_slug: str
    deployment_docker_platforms: list[str]
    deployment_python_distro: str
    deployment_ssh_host_name: str
    deployment_host_os_project_dir: str
    deployment_container_name: str
    config_file_path: str
    config_file_basename: str
    config_file_dirname: str

    _char_cleaner = re.compile(r"[^A-Za-z0-9._-]")

    @staticmethod
    def clean_chars(name: str) -> str:
        return FlatConfig._char_cleaner.sub("_", name)

    @staticmethod
    def from_config(cfg: Config) -> FlatConfig:
        slug = cfg.get_config_str("product", "slug")
        docker_platforms = cfg.get_config_list("deployment", "docker_platforms", str)
        python_distro = cfg.get_config_str("deployment", "python_distro")
        ssh_host_name = cfg.get_config_str("deployment", "ssh_host_name")
        host_os_project_dir = cfg.get_config_str("deployment", "host_os_project_dir")
        container_name = FlatConfig.clean_chars(slug)

        return FlatConfig(
            product_slug=slug,
            deployment_docker_platforms=docker_platforms,
            deployment_python_distro=python_distro,
            deployment_ssh_host_name=ssh_host_name,
            deployment_host_os_project_dir=host_os_project_dir,
            config_file_path=cfg.config_file_path,
            config_file_basename=basename(cfg.config_file_path),
            config_file_dirname=dirname(abspath(cfg.config_file_path)),
            deployment_container_name=container_name,
        )


class Config:
    def __init__(self, config_file: str):
        self.config_file_path = config_file
        self.config = self.load()

    def load(self) -> ConfigT:
        """Load and parse a TOML config file."""
        import tomllib

        with open(self.config_file_path, "rb") as f:
            try:
                return tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(
                    f"Failed to parse configuration file '{self.config_file_path}':\n{e}"
                ) from e

    def get_config_section(self, name: str) -> ConfigT:
        section = self.config.get(name, {})
        if not isinstance(section, dict):
            raise ConfigError(
                f"Section '{name}' not found in config file '{self.config_file_path}'"
            )
        return section

    def get_config_entry(self, section_name: str, entry_name: str) -> Any:
        section = self.get_config_section(section_name)
        entry = section.get(entry_name)
        if entry is None:
            raise ConfigError(
                f"Entry '{section_name}.{entry_name}' not found in config file "
                f"'{self.config_file_path}'"
            )
        return entry

    def get_config_str(self, section_name: str, entry_name: str) -> str:
        entry = self.get_config_entry(section_name, entry_name)
        if not isinstance(entry, str):
            raise ConfigError(
                f"Invalid configuration file entry '{section_name}.{entry_name}': "
                f"expected a string, found '{type(entry).__name__}'"
            )
        return entry

    def get_config_list[T: str | int | float | bool](
        self, section_name: str, entry_name: str, list_type: type[T]
    ) -> list[T]:
        entry = self.get_config_entry(section_name, entry_name)
        if not isinstance(entry, list):
            raise ConfigError(
                f"Invalid configuration file entry '{section_name}.{entry_name}': "
                f"expected a bracket list, found '{type(entry).__name__}'"
            )
        for value in entry:
            if not isinstance(value, list_type):
                raise ConfigError(
                    f"Invalid configuration file entry '{section_name}.{entry_name}': "
                    f"expected list items of type '{list_type.__name__}', "
                    f"found item '{value}' of type '{type(value).__name__}'"
                )
        return entry
