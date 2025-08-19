"""Home Assistant MQTT switch models classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations
import dataclasses
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import override

from gpiod import LineRequest
from gpiod.line import Value

from ..config.schema import SwitchGroupConfig
from ..mqtt.ha_naming import get_ha_mqtt_entity_strings, to_slug


_LOGGER = logging.getLogger(__name__)

STATE_ON = "ON"
STATE_OFF = "OFF"
STATES = [STATE_OFF, STATE_ON]


@dataclass
class MQTTSwitch:
    name: str
    unique_id: str
    config_topic: str
    state_topic: str
    availability_topic: str
    command_topic: str

    @classmethod
    def from_name(cls, mqtt_topic: str, mqtt_name: str) -> MQTTSwitch:
        entity = get_ha_mqtt_entity_strings(mqtt_topic, mqtt_name)
        unique_id, state_topic = entity.unique_id, entity.state_topic
        # Example:
        # unique_id: smart_thermostat_switch
        # name: Smart Thermostat Switch
        # config_topic: homeassistant/switch/smart_thermostat_switch/config
        # state_topic: smart_thermostat/switch
        # availability_topic: smart_thermostat/switch/available
        # command_topic: smart_thermostat/switch/set
        return cls(
            unique_id=unique_id,
            # name=unique_id.title().replace("_", " "),
            name=entity.name,
            config_topic=f"homeassistant/switch/{unique_id}/config",
            state_topic=state_topic,
            availability_topic=f"{state_topic}/available",
            command_topic=f"{state_topic}/set",
        )

    def hass_config(self) -> str:
        # https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
        return json.dumps(
            {
                **dataclasses.asdict(self),
                "qos": 2,
                "retain": True,
            }
        )


class Switch:
    """Model of a Home Assistant MQTT Switch sensor with sensor reading filters."""

    def __init__(
        self,
        mqtt_name: str,
        mqtt_topic: str,
        gpio_pin: int,
        gpio_line: LineRequest,
        keep_alive_sec: float,
        *,
        state: bool | None = None,
        slug: str = "",
    ):
        self.slug = slug or to_slug(mqtt_name)
        self.mqtt = MQTTSwitch.from_name(mqtt_topic, mqtt_name)
        self._gpio_pin = gpio_pin
        self._gpio_line = gpio_line
        self._keep_alive_sec = keep_alive_sec
        self._last_command_timestamp = time.monotonic()
        self.failsafe_triggered = False
        if state is None:
            self.state = bool(gpio_line.get_value(gpio_pin).value)
        else:
            self.state = state
            gpio_line.set_value(gpio_pin, Value(state))

    def switch(self, state: bool, failsafe_msg="", now=0.0):
        if failsafe_msg:
            self.failsafe_triggered = True
            _LOGGER.warning(
                "%s failsafe switch %s: %s",
                self.mqtt.name,
                STATES[state],
                failsafe_msg,
            )
        else:
            self.failsafe_triggered = False
            self._last_command_timestamp = now or time.monotonic()

        self.state = state
        self._set_gpio_pin()

    def _set_gpio_pin(self):
        """May be overridden for additional logic (e.g. SharedGPIOSwitch)."""
        self._gpio_line.set_value(self._gpio_pin, Value(self.state))

    def is_missing_keep_alive(self, now=0.0) -> bool:
        if not self._keep_alive_sec:
            return False
        now = now or time.monotonic()
        return now - self._last_command_timestamp >= self._keep_alive_sec


class SharedGPIOSwitch(Switch):
    """A Switch that shares a GPIO pin with other switches.

    The GPIO pin value is the result of applying logic 'or' to the state of each switch.
    """

    def __init__(
        self,
        mqtt_name: str,
        mqtt_topic: str,
        gpio_pin: int,
        gpio_line: LineRequest,
        keep_alive_sec: float,
        *,
        state: bool | None = None,
        slug: str = "",
    ):
        super().__init__(
            mqtt_name,
            mqtt_topic,
            gpio_pin,
            gpio_line,
            keep_alive_sec,
            state=state,
            slug=slug,
        )
        self._other_switches: list[Switch] = []

    def add_shared_gpio_switch(self, switch: Switch):
        self._other_switches.append(switch)

    @override
    def _set_gpio_pin(self):
        state = self.state or any(s.state for s in self._other_switches)
        self._gpio_line.set_value(self._gpio_pin, Value(state))


class SwitchGroup:
    """A iterable group of switches that may enforce "switching rules".

    Some switches in the group may have to be switched in a certain order, and
    with a certain minimum timing between switching. For example, a water valve,
    a water pump and a water heater may each be controlled by separate swiches
    in a switch group. Typically the hardware will enforce some safety measures
    such as preventing the water heater from turn on if the water pump is turned
    off. Even so, the software may be responsible for optimal operation, for
    example switching on the water valve, waiting 10 seconds for the valve to be
    fully open, then switching on the water pump, waiting 2 seconds and finally
    switching on the water heater. When turning the switches off, reverse that
    sequence and wait perhaps 30 seconds between switching the water heater off
    and switching the water pump off, so that the heating element can dissipate
    the heat and avoid localised overheat with bubbling noises.

    The Switches.switch() method switches some or all switches.
    """

    def __init__(self, cfg: SwitchGroupConfig, switches: tuple[Switch, ...]):
        self.mqtt_topic = cfg.mqtt_topic
        self._switches = switches
        self.by_slug = {sw.slug: sw for sw in switches}
        self.__last_sw_idx = -1

    def __iter__(self) -> Iterator[Switch]:
        yield from self._switches

    def get_matching_switch(self, mqtt_command_topic: str) -> Switch | None:
        for sw in self:
            if sw.mqtt.command_topic == mqtt_command_topic:
                return sw
        return None

    async def switch(
        self, state: bool, switch: Switch | None = None, failsafe_msg="", now=0.0
    ):
        """Switch a given switch, or all switches.

        A reason to call this method instead of calling switch.switch() directly
        is that the operation of some switches requires other switches to be
        switched first or later, with appropriate delays to allow for mechanical
        operation, and this knowledge is coded in speciliazed implementations of
        this method (subclasses like DualFuelSwitches).

        Args:
            state (bool): Target state
            switch (Switch | None): Target switch, or all switches if None.
            failsafe_msg (str): Indicates failsafe switching (missing keepalive
            or shutdown).
        """
        for sw in (switch,) if switch else self._switches:
            sw.switch(state, failsafe_msg, now)

    def find_missing_keepalive_switch(self, now: float) -> Switch | None:
        """Return a switch (if any) that has missed its keep-alive message."""
        n_switches = len(self._switches)
        for _ in range(n_switches):
            # Keep track of the last returned switch to continue the search in
            # the next call to this method.
            self.__last_sw_idx = (self.__last_sw_idx + 1) % n_switches
            switch = self._switches[self.__last_sw_idx]
            if not switch.failsafe_triggered and switch.is_missing_keep_alive(now):
                return switch
        return None
