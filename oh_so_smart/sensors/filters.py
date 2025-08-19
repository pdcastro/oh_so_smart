"""Noise and outlier filter classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from itertools import islice

from ..algo.linear_regression import predict, Point

_LOGGER = logging.getLogger(__name__)


class NoiseFilter:
    """Filter noise out of sensor readings.

    Sensors may produce noisy readings (measurements) even when the measured
    quantity is actually stable. For example, the DS1820B temperature sensor has
    a resolution of 0.0625°C, but some presumably low quality sensors produce
    readings that fluctuate up to 3 * 0.0625 = 0.1875°C for a constant
    temperature. To mitigate this issue, the filter() method of this class
    implements a noise filter. Check the method docstrings for more details.
    """

    def __init__(
        self,
        *,
        sensor_id: str,
        window_size: int,
        window_amplitude: float,
        stability_delta: float,
    ):
        """Initialise the noise filter.

        Args:
            sensor_id: Sensor ID string for logging purposes.
            window_size: Maximum number of sensor readings in the rolling window.
            window_amplitude: Maximum absolute difference between the smallest
                and the largest values in the window.
            stability_delta: Stability feature paremeter (see above).
        """
        self._sensor_id = sensor_id
        self._window_amplitude = window_amplitude
        self._stability_delta = stability_delta
        self._noise_window = deque[float](maxlen=window_size)
        self._previous_filtered_value = float("inf")

    def filter(self, value: float) -> float:
        """Add the given value to the filter window and return a filtered value.

        See class docstring for background info. The noise filter applies the
        following logic:

        - Maintain a rolling window of consecutive readings (first-in, first-out)
          of a configurable size (say 5 readings).
        - Let the “window amplitude” be the absolute difference between the
          minimum and the maximum values in the window.
        - When a new reading is added to the window, ensure the window amplitude
          remains no larger than the given `window_amplitude` parameter by
          discarding the oldest values until this condition is satisfied.
        - Let the “filtered value” be the median of sorted values.

        In addition, a stability feature causes the filter to return the same
        filtered value previously returned, rather than a new filtered value, if
        their absolute difference is less than the ‘stability_delta’ parameter.
        This may visually improve graphs in noisy conditions and may also help
        to compact data storage if the data recorder utilizes a counter of
        repeated consecutive sensor readings.

        Args:
            value (float): The latest sensor reading.
        """
        self._noise_window.append(value)
        start = len(self._noise_window)
        for v in reversed(self._noise_window):
            if abs(value - v) > self._window_amplitude:
                self._noise_window = deque(
                    islice(self._noise_window, start, len(self._noise_window)),
                    maxlen=self._noise_window.maxlen,
                )
                break
            start -= 1

        median = sorted(self._noise_window)[len(self._noise_window) // 2]
        if abs(median - self._previous_filtered_value) < self._stability_delta:
            filtered = self._previous_filtered_value
        else:
            filtered = self._previous_filtered_value = median

        if __debug__:
            _LOGGER.debug(
                "%s %s: window %s, median %.2F, previous %.2F filtered %.2F)",
                type(self).__name__,
                self._sensor_id,
                [f"{n:.2F}" for n in self._noise_window],
                median,
                self._previous_filtered_value,
                filtered,
            )

        return filtered


class OutlierFilter:
    """Filter outlier sensor readings out.

    Predict the next sensor reading through linear regression over a “window”
    of recent readings, and compare the prediction against the latest reading
    to decide whether the reading looks like an outlier. If it looks like an
    outlier, take additional sensor readings. Check the method docstrings for
    more details.
    """

    def __init__(self, *, sensor_id: str, outlier_delta: float, window_size: int):
        """Initialise the filter with the given parameters.

        Args:
            sensor_id: Sensor ID string for logging purposes.
            outlier_delta: Maximum acceptable absolute difference between a
              sensor reading value and the predicted value. This is the
              threshold that determines whether a sensor reading is an outlier.
            window_size: The maximum size of the outlier window.
        """
        self._sensor_id = sensor_id
        self._outlier_delta = outlier_delta
        self._outlier_window = deque[float](maxlen=window_size)

    async def filter(
        self, max_calls: int, get_value: Callable[[], Awaitable[float]]
    ) -> float:
        """Call get_value() one or more times to return a non-outlier value.

        See class docstring for the algorithm overview. Call get_value() up to
        max_calls times until it returns a value (sensor reading) that does not
        look like an outlier. The returned value is always the sensor reading
        from the most recent call to get_value().

        Args:
            max_calls: Maximum number of calls to get_value().
            get_value: async function called to get a new sensor reading.

        Returns:
            float: The sensor reading from the most recent call to get_value().
        """
        value = await get_value()
        prediction = self._predict_reading(value)

        call_count = 1
        while self._is_outlier(value, prediction, call_count):
            if call_count < max_calls:
                await asyncio.sleep(1)
                value = await get_value()
                call_count += 1
            else:
                _LOGGER.warning(
                    "'%s': Exceeded retry limit (%d >= %d) for outlier sensor readings",
                    self._sensor_id,
                    call_count,
                    max_calls,
                )
                break

        self._outlier_window.append(value)
        return value

    def _is_outlier(
        self, actual_reading: float, predicted_reading: float, call_count: int
    ) -> bool:
        is_outlier = abs(actual_reading - predicted_reading) > self._outlier_delta
        if is_outlier:
            _LOGGER.warning(
                "'%s': Outlier sensor reading (predicted %.2F, read %.2F, retry #%d)",
                self._sensor_id,
                predicted_reading,
                actual_reading,
                call_count,
            )
        elif call_count > 1:
            _LOGGER.info(
                "'%s': Recovered from outlier reading (predicted %.2F, read %.2F, retry #%d)",
                self._sensor_id,
                predicted_reading,
                actual_reading,
                call_count,
            )

        return is_outlier

    def _predict_reading(self, actual_reading: float) -> float:
        if len(self._outlier_window) > 1:
            points: list[Point] = list(enumerate(self._outlier_window))
            prediction = predict(points, len(self._outlier_window))
        else:
            prediction = actual_reading

        if __debug__:
            _LOGGER.debug(
                "%s: window %s, predicted %s, read %.2F)",
                self._sensor_id,
                [f"{n:.2F}" for n in self._outlier_window],
                f"{prediction:.2F}" if prediction is not None else "None",
                actual_reading,
            )

        return prediction
