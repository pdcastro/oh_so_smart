"""Test code for the linear_regression module.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import unittest

from ...algo.linear_regression import Point, predict


class TestLinearRegression(unittest.TestCase):
    def test_best_fit_predict(self):
        test_inputs: list[tuple[list[Point], tuple[float, float]]] = [
            ([(0, 0), (1, 1)], (2, 2)),
            ([(1, 2), (2, 3), (3, 4)], (4, 5)),
            ([(3.0, 3.5), (5.0, 5.0), (10.0, 5.0)], (7, 4.67307692)),
        ]
        for data_points, prediction in test_inputs:
            given_x, expected_y = prediction
            # b, m = best_fit(data_points)
            # print(f"y = {b} + {m} * x, predict({given_x})={predict(data_points, given_x)}")
            self.assertAlmostEqual(predict(data_points, given_x), expected_y)


# Tests can be run with the command line:
# python -m unittest oh_so_smart.test.algo.test_linear_regression
if __name__ == "__main__":
    unittest.main()
