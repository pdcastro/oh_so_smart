"""Command-line argument parser.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import argparse
from dataclasses import dataclass


@dataclass
class CmdArgs:
    config_file: str


def get_package_name() -> str:
    from os.path import abspath, basename, dirname

    return basename(dirname(abspath(__file__)))


def parse_command_line() -> CmdArgs:
    parser = argparse.ArgumentParser(prog=get_package_name())
    parser.add_argument(
        "-c",
        "--config-file",
        required=True,
        help="TOML configuration file path",
    )
    args = CmdArgs(**vars(parser.parse_args()))

    return args
