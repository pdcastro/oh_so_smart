"""Supporting factory functions for product customisations.

Factory functions are used to make product-specific objects of common base classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from asyncio import AbstractEventLoop
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Protocol

from gpiod import LineRequest

from ..cmdline_parser import CmdArgs
from ..config.schema import (
    CoreConfig,
    PreValidationConfig,
    SwitchGroupConfig,
    load_config_file,
)
from ..sensors.sensors import TemperatureSensorGroup
from ..switches.switches import Switch, SwitchGroup


class Manager(Protocol):
    async def start(self): ...


@dataclass
class CoreObjects:
    cmd_args: CmdArgs
    pre_config: PreValidationConfig
    loop: AbstractEventLoop


@dataclass
class ProductObjects:
    config: CoreConfig
    switch_groups: list[SwitchGroup] = field(default_factory=list)
    temperature_sensor_groups: list[TemperatureSensorGroup] = field(
        default_factory=list
    )
    manager: Manager | None = None


def make_objects(args: CmdArgs, loop: AbstractEventLoop) -> ProductObjects:
    cfg = load_config_file(args.config_file)
    factory = import_module(f".{cfg.product_source_dir}.factory", __package__)
    return factory.make_objects(CoreObjects(args, cfg, loop))


def make_switches(
    group_cfg: SwitchGroupConfig, gpio_line: LineRequest
) -> tuple[Switch, ...]:
    mqtt_topic = group_cfg.mqtt_topic
    return tuple(
        Switch(
            mqtt_name=sw.mqtt_name,
            mqtt_topic=mqtt_topic,
            gpio_pin=sw.gpio_pin,
            gpio_line=gpio_line,
            keep_alive_sec=group_cfg.keep_alive_timeout_sec,
            slug=sw.slug,
        )
        for sw in group_cfg.switches
    )


def make_switch_group(
    group_cfg: SwitchGroupConfig, gpio_line: LineRequest
) -> SwitchGroup:
    return SwitchGroup(group_cfg, make_switches(group_cfg, gpio_line))


def make_switch_groups(
    cfg_switch_groups: Iterable[SwitchGroupConfig], gpio_line: LineRequest
) -> list[SwitchGroup]:
    return [make_switch_group(group, gpio_line) for group in cfg_switch_groups]


def make_temperature_sensor_groups(cfg: CoreConfig) -> list[TemperatureSensorGroup]:
    return [
        TemperatureSensorGroup(group_cfg) for group_cfg in cfg.temperature_sensor_groups
    ]
