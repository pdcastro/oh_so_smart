"""Supporting functions to setup GPIO pins given configuration models.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from collections.abc import Iterable
from datetime import timedelta
from typing import Union, Optional

from gpiod import (
    request_lines,
    LineRequest,
    LineSettings,
)
from gpiod.line import Direction, Bias, Edge

from ..config.gpio import GPIOConfig
from ..config.schema import CoreConfig

GPIO_LINE_PATH = "/dev/gpiochip0"


class GPIOInterface:
    def __init__(self, line_request: LineRequest, gpio_cfgs: Iterable[GPIOConfig]):
        self.line_req = line_request
        self.by_slug = {cfg.slug: cfg for cfg in gpio_cfgs}


def setup_gpio(gpio_cfgs: Iterable[GPIOConfig], gpio_consumer: str) -> GPIOInterface:
    """Set up the operation mode of GPIO pins."""
    # Full type annotation to appease pyright
    line_config: dict[
        Union[Iterable[Union[int, str]], int, str], Optional[LineSettings]
    ] = {
        g.pin: LineSettings(
            direction=Direction[g.direction.upper()],
            bias=Bias[g.bias.upper()] if g.bias else Bias.AS_IS,
            edge_detection=(
                Edge[g.edge_detection.upper()] if g.edge_detection else Edge.NONE
            ),
            debounce_period=(
                timedelta(milliseconds=g.debounce_period_ms)
                if g.debounce_period_ms
                else timedelta()
            ),
        )
        for g in gpio_cfgs
    }
    try:
        line_req = request_lines(
            path=GPIO_LINE_PATH,
            consumer=gpio_consumer,
            config=line_config,
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(GPIO_LINE_PATH + f" ({e})") from e

    return GPIOInterface(line_req, gpio_cfgs)


def setup_grouped_out_pins(
    out_pins: tuple[int, ...], gpio_consumer: str
) -> LineRequest:
    try:
        return request_lines(
            path=GPIO_LINE_PATH,
            consumer=gpio_consumer,
            config={
                out_pins: LineSettings(direction=Direction.OUTPUT),
            },
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(GPIO_LINE_PATH + f" ({e})") from e


def setup_gpio_out_pins(cfg: CoreConfig) -> LineRequest:
    switch_pins = {sw.gpio_pin for group in cfg.switch_groups for sw in group.switches}
    other_out_pins = {g.pin for g in cfg.gpio if g.direction == "output"}
    return setup_grouped_out_pins(
        tuple(switch_pins | other_out_pins),
        gpio_consumer=cfg.product.slug,
    )
