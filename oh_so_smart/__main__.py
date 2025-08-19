"""Package entrypoint code (executed by a ‘python -m <package-name>’ command line).

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import sys


def mock_gpiod():
    """Conditionally and partially mock the ‘gpiod’ package.

    This is a limited mocking of the gpiod package that allows it to be imported,
    but not actually used, on unsupported systems such as Windows and macOS.
    """
    from platform import system

    if system() != "Linux":
        from unittest.mock import Mock

        sys.modules["gpiod"] = Mock()
        sys.modules["gpiod.line"] = Mock()


def config_w1thermsensor():
    """Configure the ‘w1thermsensor’ package."""
    from os import environ

    # Prevent the ‘w1thermsensor’ package from attempting to load the
    # ‘w1-therm’ and ‘w1-gpio’ Linux kernel modules upon import.
    environ["W1THERMSENSOR_NO_KERNEL_MODULE"] = "1"


if __name__ == "__main__":
    mock_gpiod()
    config_w1thermsensor()

    from .main import main

    exit_code = main()
    sys.exit(exit_code)
