"""Linear Regression utility functions.

Adapted from the post by Joshua Davies at:
https://commandlinefanatic.com/cgi-bin/showarticle.cgi?article=art084

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from collections.abc import Collection

type Point = tuple[float, float]


def stats(dp: Collection[Point], i: int) -> float:
    """Compute the average of a column of data.

    Args:
        df (list[Point]): Input data points
        i (int): Index of point axis x or y to average

    Returns:
        float: The average of a column of data
    """
    ave = sum(p[i] for p in dp) / len(dp)

    return ave


def best_fit(dp: Collection[Point]) -> tuple[float, float]:
    """Calculate linear regression coeficients (b, m) for the curve y = b + m * x.

    Args:
        dp (list[Point]): List of input data points

    Returns:
        tuple[float, float]: (b, m) coefficients for the curve y = b + m * x
    """
    ave_x = stats(dp, 0)
    ave_y = stats(dp, 1)
    m = sum(p[0] * (p[1] - ave_y) for p in dp) / sum(p[0] * (p[0] - ave_x) for p in dp)
    b = ave_y - m * ave_x

    return (b, m)


def predict(dp: Collection[Point], x: float) -> float:
    """Comput value y = b + m * x for the given value x."""
    b, m = best_fit(dp)
    return b + m * x
