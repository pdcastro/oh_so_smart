"""Factory functions for the Dual Fuel Thermostat product.

Factory functions are used to make product-specific objects of common base classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from gpiod import LineRequest

from ...config.schema import CoreConfig, validate_config
from ...switches.switches import SwitchGroup
from ..factory import (
    CoreObjects,
    ProductObjects,
    make_temperature_sensor_groups,
)
from ..gpio import setup_gpio_out_pins
from .dual_fuel_switches import DualFuelSwitchGroup, SwitchSlugs


class SmartThermostatConfig(CoreConfig):
    """Optional specialization of the configuration file schema."""


def _make_switch_groups(
    cfg: SmartThermostatConfig, gpio_line: LineRequest
) -> list[SwitchGroup]:
    switch_groups: list[SwitchGroup] = []
    for group_cfg in cfg.switch_groups:
        try:
            hw_select_gpio_pin = next(
                g.pin for g in cfg.gpio if g.slug == SwitchSlugs.HW_SELECT
            )
        except StopIteration as exc:
            raise Exception(
                f"Missing {SwitchSlugs.HW_SELECT} GPIO pin configuration"
            ) from exc
        group = DualFuelSwitchGroup(group_cfg, hw_select_gpio_pin, gpio_line)

        switch_groups.append(group)

    return switch_groups


def make_objects(core: CoreObjects) -> ProductObjects:
    # pylint: disable=R0801
    # R0801: Similar lines in 2 files
    cfg = validate_config(core.pre_config.config_dict, SmartThermostatConfig)
    gpio_line = setup_gpio_out_pins(cfg)
    switch_groups = _make_switch_groups(cfg, gpio_line)
    temperature_sensor_groups = make_temperature_sensor_groups(cfg)
    return ProductObjects(
        config=cfg,
        switch_groups=switch_groups,
        temperature_sensor_groups=temperature_sensor_groups,
    )
