"""Supporting error handling classes for the sensors package.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import logging

_LOGGER = logging.getLogger(__name__)


class SensorError(Exception):
    pass


class SensorErrorSession:
    """A session (period of time) during which a sensor is unavailable."""

    def __init__(self, sensor_name: str):
        self._sensor_name = sensor_name
        self._err_count = 0
        self._err_skip_count = 0.0
        self._err_last_skip_count = 0.0

    def _get_msg(self, exc: Exception) -> str:
        return f"Error reading '{self._sensor_name}': {exc}"

    def add_error(self, exc: Exception) -> int:
        self._err_count += 1
        if self._err_skip_count <= 0:
            _LOGGER.warning(
                "%s. Will retry often, but won't print this as often. (Err count %d)",
                self._get_msg(exc),
                self._err_count,
            )
            self._err_skip_count = 1.5 * (1 + self._err_last_skip_count)
            self._err_last_skip_count = self._err_skip_count
        else:
            self._err_skip_count -= 1

        return self._err_count

    def get_error(self, exc: Exception) -> SensorError:
        return SensorError(self._get_msg(exc))
