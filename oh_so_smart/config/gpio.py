"""GPIO configuration model classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from typing import Any, Literal, Optional, Self, override

from pydantic import BaseModel, field_validator, model_validator

from .rpi_pin_map import BOARD_TO_CHIP


class GPIOConfig(BaseModel):
    slug: str  # Short descriptive name like 'demand_output'
    pin: int
    direction: Literal["as_is", "input", "output"]
    bias: Optional[Literal["as_is", "disabled", "pull_up", "pull_down"]] = None
    edge_detection: Optional[Literal["none", "rising", "falling", "both"]] = None
    debounce_period_ms: int = 0  # 0 disables debouncing

    @model_validator(mode="after")
    def validate_bias(self) -> Self:
        if self.direction == "input" and not self.bias:
            raise ValueError(
                "The GPIO 'bias' field must be provided when the direction is 'input'"
            )
        if self.direction == "output" and self.bias:
            raise ValueError(
                "The GPIO 'bias' field must not be provided when the direction is 'output'"
            )
        if self.direction == "as_is" and self.bias and self.bias != "as_is":
            raise ValueError(
                "The GPIO 'bias' field must either not be provided or have value 'as_is' when the direction is 'as_is'"
            )
        if self.direction != "input" and (
            self.debounce_period_ms
            or (self.edge_detection and self.edge_detection != "none")
        ):
            raise ValueError(
                "The GPIO 'debounce_period' or 'edge_detection' fields can only be provided when the direction is 'input'"
            )
        return self

    # pylint incorrectly reports that the parent has a different number of arguments.
    # pylint: disable=arguments-differ
    @override
    def model_post_init(self, context: Any):
        """Translate board header pin numbers to chip (Broadcom) pin numbers."""
        self.pin = BOARD_TO_CHIP[self.pin]
        super().model_post_init(context)


class GPIOPinConfig(BaseModel):
    """GPIO pin number configuration.

    The TOML configuration file use the Raspberry Pi board header pin numbering.
    This class automatically maps the pin numbers to the internal chip (Broadcom)
    numbering through the `__post_init__` method.
    """

    gpio_pin: int

    # pylint incorrectly reports that the parent has a different number of arguments.
    # pylint: disable=arguments-differ
    @override
    def model_post_init(self, context: Any):
        """Translate board header pin numbers to chip (Broadcom) pin numbers."""
        self.gpio_pin = BOARD_TO_CHIP[self.gpio_pin]
        super().model_post_init(context)


class W1GPIOConfig(BaseModel):
    """GPIO pin number configuration."""

    w1_gpio_pin: int  # 1-wire protocol for DS18B20 temperature sensors

    @field_validator("w1_gpio_pin")
    @classmethod
    def must_be_seven[T](cls, value: T) -> T:
        if value != 7:
            raise ValueError(f"'w1_gpio_pin' must be '7', found '{value}' instead")
        return value
