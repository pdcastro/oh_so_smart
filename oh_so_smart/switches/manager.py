"""Home Assistant MQTT switch manager class.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio
import logging
import time
from collections.abc import Iterable

import paho.mqtt.client as pmcli

from ..mqtt.msg import Msg, MsgType
from ..mqtt.queue import MsgQueue
from .switches import STATES, MQTTSwitch, Switch, SwitchGroup

_LOGGER = logging.getLogger(__name__)


class SwitchNotFoundError(Exception):
    pass


class HassSwitchManager:
    """Monitor Home Assistant MQTT switches and actuate GPIO pins.

    The start() method publishes MQTT switch entities for Home Assistant auto
    discovery, and starts a long-lived loop that reads on/off switch “commands”
    from the message receive queue, acts on the respective GPIO pins, and
    appends switch state “responses” to the message send queue.
    """

    def __init__(
        self,
        send_queue: MsgQueue[Msg],
        recv_queue: MsgQueue[pmcli.MQTTMessage],
        switch_groups: Iterable[SwitchGroup],
    ):
        self._send_queue = send_queue
        self._recv_queue = recv_queue
        self._switch_groups = switch_groups
        self._last_recv_msg: tuple[str, bytes | bytearray] | None = None

    async def start(self):
        _LOGGER.info("%s: starting", type(self).__name__)

        await self._register_with_home_assistant()

        try:
            # Note: CancelledError may be raised here
            await self._monitor_mqtt_queue()
        finally:
            _LOGGER.info("%s: shutting down", type(self).__name__)
            await self._shutdown()

    async def _register_mqtt_switch(self, sw: MQTTSwitch):
        # For graceful exit
        await self._send_queue.put(
            Msg(sw.availability_topic, "offline", type=MsgType.ON_EXIT)
        )
        # For ungraceful exit
        await self._send_queue.put(
            Msg(sw.availability_topic, "offline", type=MsgType.MQTT_WILL)
        )
        # HASS MQTT discovery message
        await self._send_queue.put(
            Msg(sw.config_topic, sw.hass_config(), type=MsgType.ON_CONNECT)
        )
        await self._send_queue.put(
            Msg(sw.availability_topic, "online", type=MsgType.ON_CONNECT)
        )
        # Subscribe to HASS on/off switch commands
        await self._send_queue.put(Msg(sw.command_topic, type=MsgType.SUBSCRIBE))

    async def _register_with_home_assistant(self):
        for switch_group in self._switch_groups:
            for sw in switch_group:
                await self._register_mqtt_switch(sw.mqtt)

    async def _monitor_mqtt_queue(self):
        """Consume the MQTT recv queue for on/off switch commands from Home Assistant."""

        # Note that this loop may still get interrupted by CancelledError
        # if any other sibling task in a TaskGroup raises an exception, or
        # if any parent task gets cancelled.
        while True:
            try:
                msg = await self._recv_queue.get(1)
            except TimeoutError:
                now = time.monotonic()
                for group in self._switch_groups:
                    if switch := group.find_missing_keepalive_switch(now):
                        await self._switch_and_update_mqtt_state(
                            False, group, switch, "Missing HASS keep alive command"
                        )
                continue

            self._print_received_msg(msg)

            # Any message in the recv queue should be a switch on/off msg.
            switch: Switch | None = None
            switch_group: SwitchGroup | None = None
            for group in self._switch_groups:
                if switch := group.get_matching_switch(msg.topic):
                    switch_group = group
                    break
            if not switch or not switch_group:
                _LOGGER.error(
                    "Error: unexpected MQTT command message topic: %s",
                    msg.topic,
                )
                continue

            state = False
            if msg.payload not in (b"ON", b"OFF"):
                _LOGGER.error(
                    "Error: unexpected MQTT payload '%s' for topic '%s'",
                    msg.payload,
                    msg.topic,
                )
            else:
                if msg.payload == b"ON":
                    state = True

            await self._switch_and_update_mqtt_state(state, switch_group, switch)

    async def _switch_and_update_mqtt_state(
        self, state: bool, group: SwitchGroup, switch: Switch, failsafe_msg=""
    ):
        await group.switch(state, switch, failsafe_msg)
        msg = Msg(switch.mqtt.state_topic, STATES[switch.state])
        # Impose a maximum wait time (wait_for()) because, as currently implemented,
        # the failsafe switch off on missing keepalive messages runs on the same loop
        # as the mqtt recv and send queues. The loop must not be allowed to block.
        try:
            await asyncio.wait_for(self._send_queue.put(msg), 1)
        except TimeoutError:
            _LOGGER.error(
                "Timeout waiting for mqtt send queue to notify HASS of switch state change (%s)",
                msg.topic,
            )

    async def _shutdown(self, msg="Shutdown"):
        # Turn all switches off
        for group in self._switch_groups:
            await group.switch(False, None, msg)

        _LOGGER.info("%s: %s", type(self).__name__, msg)

    def _print_received_msg(self, msg: pmcli.MQTTMessage):
        if self._last_recv_msg == (msg.topic, msg.payload):
            return
        self._last_recv_msg = (msg.topic, msg.payload)
        try:
            payload = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload = msg.payload

        _LOGGER.debug("Received from '%s': '%s'", msg.topic, payload)
