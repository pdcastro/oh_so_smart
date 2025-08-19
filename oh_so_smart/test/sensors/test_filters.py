"""Test code for the sensors.filters module.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import unittest
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from unittest.mock import Mock, patch

from ...sensors.filters import NoiseFilter, OutlierFilter


@dataclass
class NoiseFilterTestCase:
    nf: NoiseFilter
    measurements: list[float]
    expected_windows: list[list[float]]
    expected_filtered_values: list[float]


class TestNoiseFilter(unittest.TestCase):
    def _make_filter(self, sensor_id: str):
        return NoiseFilter(
            sensor_id=sensor_id,
            window_size=5,
            window_amplitude=0.2,
            stability_delta=0.09,
        )

    def _assert_test_case(self, nfc: NoiseFilterTestCase):
        for measurement, expected_window, expected_value in zip(
            nfc.measurements,
            nfc.expected_windows,
            nfc.expected_filtered_values,
            strict=True,
        ):
            filtered = nfc.nf.filter(measurement)
            self.assertEqual(
                list(nfc.nf._noise_window),  # pylint: disable=protected-access
                expected_window,
                "window != expected window",
            )
            self.assertEqual(filtered, expected_value, "measurement != expected")

    def test_slow_increase(self):
        nfc = NoiseFilterTestCase(
            nf=self._make_filter("slow_increase"),
            measurements=[1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3],
            expected_windows=[
                [1.0],
                [1.0, 1.05],
                [1.0, 1.05, 1.1],
                [1.0, 1.05, 1.1, 1.15],
                [1.0, 1.05, 1.1, 1.15, 1.2],
                [1.05, 1.1, 1.15, 1.2, 1.25],
                [1.1, 1.15, 1.2, 1.25, 1.3],
            ],
            expected_filtered_values=[1.0, 1.0, 1.0, 1.1, 1.1, 1.1, 1.2],
        )
        self._assert_test_case(nfc)

    def test_fast_increase(self):
        nfc = NoiseFilterTestCase(
            nf=self._make_filter("fast_increase"),
            measurements=[1.0, 1.1, 1.2, 1.3, 1.4],
            expected_windows=[
                [1.0],
                [1.0, 1.1],
                [1.0, 1.1, 1.2],
                [1.1, 1.2, 1.3],
                [1.2, 1.3, 1.4],
            ],
            expected_filtered_values=[1.0, 1.1, 1.1, 1.2, 1.3],
        )
        self._assert_test_case(nfc)

    def test_out_of_order(self):
        nfc = NoiseFilterTestCase(
            nf=self._make_filter("out_of_order"),
            measurements=[1.2, 1.0, 1.4, 1.3, 1.1],
            expected_windows=[
                [1.2],
                [1.2, 1.0],
                [1.4],
                [1.4, 1.3],
                [1.3, 1.1],
            ],
            expected_filtered_values=[1.2, 1.2, 1.4, 1.4, 1.3],
        )
        self._assert_test_case(nfc)

    def test_negative_measurements(self):
        nfc = NoiseFilterTestCase(
            nf=NoiseFilter(
                sensor_id="negative_measurements",
                window_size=5,
                window_amplitude=0.2,
                stability_delta=0.09,
            ),
            measurements=[-1.2, -1.15, -1.0, -0.9, 1.0, -1.0],
            expected_windows=[
                [-1.2],
                [-1.2, -1.15],
                [-1.2, -1.15, -1.0],
                [-1.0, -0.9],
                [1.0],
                [-1.0],
            ],
            expected_filtered_values=[-1.2, -1.2, -1.2, -0.9, 1.0, -1.0],
        )
        self._assert_test_case(nfc)


@dataclass
class OutlierFilterTestCase:
    of: OutlierFilter
    max_calls: int
    measurements: list[float]
    expected_windows: list[list[float]]
    expected_get_value_call_count: list[int]
    expected_values: list[float]


@patch("asyncio.sleep", autospec=True)
class TestOutlierFilter(unittest.IsolatedAsyncioTestCase):
    def _mock_get_value(
        self, values_to_return: Iterable[float]
    ) -> tuple[Mock, Callable[[], Awaitable[float]]]:
        get_value_mock = Mock()
        get_value_mock.side_effect = values_to_return

        async def get_value() -> float:
            return get_value_mock()

        return get_value_mock, get_value

    async def _assert_test_case(self, tc: OutlierFilterTestCase):
        get_value_mock, get_value = self._mock_get_value(tc.measurements)

        for expected_window, expected_call_count, expected_value in zip(
            tc.expected_windows,
            tc.expected_get_value_call_count,
            tc.expected_values,
            strict=True,
        ):
            filtered = await tc.of.filter(tc.max_calls, get_value)
            self.assertEqual(
                list(tc.of._outlier_window),  # pylint: disable=protected-access
                expected_window,
                "window != expected window",
            )
            self.assertEqual(filtered, expected_value, "filtered != expected")
            self.assertEqual(
                get_value_mock.call_count,
                expected_call_count,
                f"expected {expected_call_count} calls to get_value(), got {get_value_mock.call_count}",
            )

    async def test_outlier_increasing_readings(self, _mock_sleep):
        tc = OutlierFilterTestCase(
            of=OutlierFilter(
                sensor_id="increasing_readings", window_size=3, outlier_delta=3.0
            ),
            max_calls=3,
            measurements=[10.0, 11.0, 12.0, 30.0, 31.0, 32.0, 50.0, 40.0],
            expected_windows=[
                [10.0],
                [10.0, 11.0],
                [10.0, 11.0, 12.0],
                [11.0, 12.0, 32.0],
                [12.0, 32.0, 40.0],
            ],
            expected_get_value_call_count=[1, 2, 3, 6, 8],
            expected_values=[10.0, 11.0, 12.0, 32.0, 40.0],
        )
        await self._assert_test_case(tc)

    async def test_outlier_negative_readings(self, _mock_sleep):
        tc = OutlierFilterTestCase(
            of=OutlierFilter(
                sensor_id="negative_readings", window_size=3, outlier_delta=3.0
            ),
            max_calls=2,
            measurements=[-5, -6, -7, 0, 5, 6, 7, 8, 9],
            expected_windows=[
                [-5],
                [-5, -6],
                [-5, -6, -7],
                [-6, -7, 5],
                [-7, 5, 6],
                [5, 6, 8],
                [6, 8, 9],
            ],
            expected_get_value_call_count=[1, 2, 3, 5, 6, 8, 9],
            expected_values=[-5, -6, -7, 5, 6, 8, 9],
        )
        await self._assert_test_case(tc)


# Tests can be run with the command line:
# python -m unittest oh_so_smart.test.sensors.test_filters
if __name__ == "__main__":
    unittest.main()
