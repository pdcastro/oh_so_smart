"""Temperature sensor manager class.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import asyncio
import logging

from ..algo.sleeper import RegularSleeper
from ..mqtt.msg import Msg, MsgType
from ..mqtt.queue import MsgQueue
from .error import SensorError
from .sensors import TemperatureSensor, TemperatureSensorGroup

_LOGGER = logging.getLogger(__name__)


class TemperatureSensorManager:
    """Publish periodic temperature sensor readings for Home Assistant.

    The start() method creates asyncio tasks to periodically publish
    sensor reading MQTT messages for consumption by Home Assistant.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        send_queue: MsgQueue[Msg],
        sensor_groups: list[TemperatureSensorGroup],
    ):
        self._loop = loop
        self._send_queue = send_queue
        self._sensor_groups = sensor_groups

    async def start(self):
        try:
            _LOGGER.info("%s: starting", type(self).__name__)
            async with asyncio.TaskGroup() as tg:
                for group in self._sensor_groups:
                    tg.create_task(self._publish_group_temperatures(group))
        finally:
            _LOGGER.info("%s: shutting down", type(self).__name__)

    async def _publish_group_temperatures(self, group: TemperatureSensorGroup):
        await self._register_with_home_assistant(group)

        sleeper = RegularSleeper(group.poll_interval_sec, self._loop)

        # Note that this loop will get interrupted by a CancelledError
        # exception if any sibling task in a TaskGroup raises an exception,
        # or if a parent task is cancelled.
        while True:
            async with asyncio.TaskGroup() as tg:
                for sensor in group.sensors:
                    tg.create_task(self._publish_temperature(sensor, group))

            await sleeper.sleep()

    async def _publish_temperature(
        self, sensor: TemperatureSensor, group: TemperatureSensorGroup
    ):
        try:
            temp = await sensor.get_temperature()
        except SensorError:
            if group.tolerate_missing_sensors:
                return
            else:
                raise

        await self._send_queue.put(Msg(sensor.mqtt.state_topic, f"{temp:.2F}"))

    async def _register_with_home_assistant(self, group: TemperatureSensorGroup):
        for sensor in group.sensors:
            # HASS MQTT discovery message
            await self._send_queue.put(
                Msg(
                    sensor.mqtt.config_topic,
                    sensor.mqtt.hass_config(),
                    type=MsgType.ON_CONNECT,
                )
            )
