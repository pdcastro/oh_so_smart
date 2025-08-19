"""Implementation of Home Assistant MQTT switches that do not map directly to GPIO pins.

Instead, the GPIO pins are controlled by custom logic in Python source code.

Consider a heating system fitted with both electrical and natural gas heating
sources. By user preference, each heating source is modeled with its own Home
Assistant Climate integration instance:

- A “Gas Heating” climate integration instance.
- An “Electric Heating” climate integration instance.

Accordingly, the smart thermostat device exposes two Home Assistant MQTT
switches, one for each climate integration. However, at the hardware level,
the controls are different:

- A “heat demand” GPIO pin: on = turn heating on, off = turn heating off.
- A “fuel select” GPIO pin: on = electricity, off = natural gas.

The hardware thus prevents both heating sources from being active at the same
time.

The DualFuelSwitchGroup class in this file implements the following logic to map
the state of the Home Assistant MQTT switches to the state of the GPIO pins:

   | Gas Switch | Electrical  || ‘Heat Demand’  | ‘Fuel Select’  |
   |   (Input)  | Switch (In) || GPIO pin (Out) | GPIO pin (Out) |
   |      0     |      0      ||        0       |       0        |
   |      0     |      1      ||        1       |       1        |
   |      1     |      0      ||        1       |       0        |
   |      1     |      1      ||        1       |       1        |

The implementation also uses the SharedGPIOSwitch class that reflects the fact
that the ‘heat demand’ GPIO pin is shared by both switches.

---
Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio
import logging
from enum import StrEnum
from typing import override

from gpiod import LineRequest
from gpiod.line import Value

from ...config.schema import SwitchGroupConfig, SwitchConfig
from ...switches.switches import Switch, SharedGPIOSwitch, SwitchGroup


_LOGGER = logging.getLogger(__name__)


class SwitchSlugs(StrEnum):
    """Switch slugs (stable identifiers that can be used in source code)."""

    EHW = "ehw_hass"  # Electric Hot Water Home Assistant programmer switch
    GHW = "ghw_hass"  # Gas Hot Water Home Assistant programmer switch
    HW_SELECT = "hw_select"  # Hot Water fuel select (gas vs electric) switch


def _find_switch_config(cfg: SwitchGroupConfig) -> tuple[SwitchConfig, SwitchConfig]:
    """Find the SwitchConfig objects for the EHW and GHW switches."""
    by_slug = {sw_cfg.slug: sw_cfg for sw_cfg in cfg.switches}
    try:
        return (by_slug[SwitchSlugs.EHW], by_slug[SwitchSlugs.GHW])
    except KeyError as exc:
        raise Exception(
            f"Virtual switch configuration not found ({','.join(SwitchSlugs)})"
        ) from exc


def _make_virtual_switches(
    cfg: SwitchGroupConfig, hw_select_pin: int, gpio_line: LineRequest
) -> tuple[SharedGPIOSwitch, SharedGPIOSwitch]:
    """Create MQTT switches for Electric Hot Water (EHW) and Gas Hot Water (GHW).

    See module docstring (at the top of this module) for an overview of the solution.
    """
    # ehw = electric hot water, ghw = gas hot water
    ehw_cfg, ghw_cfg = _find_switch_config(cfg)

    select_state = bool(gpio_line.get_value(hw_select_pin))
    hw_state = bool(gpio_line.get_value(ehw_cfg.gpio_pin))
    ehw_state = hw_state and select_state
    ghw_state = hw_state and not select_state

    ehw_sw = SharedGPIOSwitch(
        slug=SwitchSlugs.EHW,
        mqtt_name=ehw_cfg.mqtt_name,
        mqtt_topic=cfg.mqtt_topic,
        gpio_pin=ehw_cfg.gpio_pin,
        gpio_line=gpio_line,
        keep_alive_sec=cfg.keep_alive_timeout_sec,
        state=ehw_state,
    )
    ghw_sw = SharedGPIOSwitch(
        slug=SwitchSlugs.GHW,
        mqtt_name=ghw_cfg.mqtt_name,
        mqtt_topic=cfg.mqtt_topic,
        gpio_pin=ghw_cfg.gpio_pin,
        gpio_line=gpio_line,
        keep_alive_sec=cfg.keep_alive_timeout_sec,
        state=ghw_state,
    )
    ehw_sw.add_shared_gpio_switch(ghw_sw)
    ghw_sw.add_shared_gpio_switch(ehw_sw)

    return (ehw_sw, ghw_sw)


class DualFuelSwitchGroup(SwitchGroup):
    """SwitchGroup model for switches that require non-standard handling.

    See module docstring (at the top of this module) for an overview of the solution.

    The two GPIO pins involved in the control of hot water heating should be turned
    on/off in a certain order and timing for optimal system performance. When turning
    on hot water heating, the hw_select pin should be operated first, and one second
    later the shared hot water demand pin. When turning off hot water heating, this
    sequence is inverted.
    """

    def __init__(
        self, cfg: SwitchGroupConfig, hw_select_pin: int, gpio_line: LineRequest
    ):
        super().__init__(cfg, _make_virtual_switches(cfg, hw_select_pin, gpio_line))
        # GPIO pin that selects between gas and electricity for hot water (hw) heating
        self._hw_select_pin = hw_select_pin
        self._gpio_line = gpio_line

    @override
    async def switch(
        self, state: bool, switch: Switch | None = None, failsafe_msg="", now=0.0
    ):
        """Implement the GPIO switching order and timing described in the class docstring.

        Args:
            state: Whether the switch(es) should be turned on (True) or off (False).
            switch: Switch to turn on/off. None means all switches in the group.
            failsafe_msg: Log message for failsafe (keep-alive timeout) switching.
            now: Python time.monotonic() timestamp for keep-alive timeout computation.
        """
        slug = switch.slug if switch else None
        _LOGGER.debug("%s: slug=%s state=%s", type(self).__name__, slug, state)

        ehw_slug = SwitchSlugs.EHW  # Electric Hot Water virtual switch
        ghw_slug = SwitchSlugs.GHW  # Gas Hot Water virtual switch

        if slug in (None, ehw_slug, ghw_slug):
            ehw = self.by_slug[ehw_slug]
            ghw = self.by_slug[ghw_slug]
            target_ehw = state if slug in (None, ehw_slug) else ehw.state
            target_ghw = state if slug in (None, ghw_slug) else ghw.state
            target_hw = target_ehw or target_ghw
            target_select = target_ehw

            def select_hw():
                self._gpio_line.set_value(self._hw_select_pin, Value(target_select))

            def switch_hw():
                if slug in (None, ehw_slug):
                    ehw.switch(target_ehw, failsafe_msg, now)
                if slug in (None, ghw_slug):
                    ghw.switch(target_ghw, failsafe_msg, now)

            # When turning HW on, first turn on the hot water select pin, then
            # the hot water pin. When turning off, invert the sequence.
            actions = (select_hw, switch_hw) if target_hw else (switch_hw, select_hw)
            actions[0]()
            await asyncio.sleep(1)
            actions[1]()
