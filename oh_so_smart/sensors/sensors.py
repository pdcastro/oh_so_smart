"""Temperature sensor model classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from __future__ import annotations
import asyncio
import dataclasses
import json
from dataclasses import dataclass

from w1thermsensor import AsyncW1ThermSensor
from w1thermsensor.sensors import Sensor as W1Sensor
from w1thermsensor.errors import (
    NoSensorFoundError,
    ResetValueError,
    SensorNotReadyError,
)

from ..config.schema import TemperatureSensorConfig, TemperatureSensorGroupConfig
from ..mqtt.ha_naming import get_ha_mqtt_entity_strings
from .error import SensorErrorSession
from .filters import OutlierFilter, NoiseFilter


@dataclass
class MQTTSensor:
    name: str  # E.g. 'living_room_temperature'
    unique_id: str  # E.g. 'smart_thermostat_living_room_temperature'
    config_topic: str  # E.g. 'homeassistant/sensor/smart_thermostat_living_room_temperature/config'
    state_topic: str  # E.g. 'smart_thermostat/living_room_temperature'
    device_class: str = "temperature"
    state_class: str = "measurement"
    unit_of_measurement: str = "°C"
    suggested_display_precision: int = 2  # number of decimals

    @classmethod
    def from_name(cls, mqtt_topic: str, mqtt_name: str) -> MQTTSensor:
        entity = get_ha_mqtt_entity_strings(mqtt_topic, mqtt_name)
        unique_id = entity.unique_id
        return cls(
            unique_id=unique_id,
            name=entity.name,
            config_topic=f"homeassistant/sensor/{unique_id}/config",
            state_topic=entity.state_topic,
        )

    def hass_config(self) -> str:
        # https://www.home-assistant.io/integrations/sensor.mqtt/
        return json.dumps(
            {
                **dataclasses.asdict(self),
                "force_update": True,
                "expire_after": 600,  # seconds
                "qos": 2,
            }
        )


class TemperatureSensorGroup:
    def __init__(
        self,
        cfg: TemperatureSensorGroupConfig,
    ):
        self.poll_interval_sec = cfg.poll_interval_sec
        self.tolerate_missing_sensors = cfg.tolerate_missing_sensors
        self.sensors = tuple(
            TemperatureSensor.from_config(sensor, cfg.mqtt_topic)
            for sensor in cfg.sensors
        )


class TemperatureSensor:
    """Model of a Home Assistant MQTT temperature sensor with sensor reading filters."""

    def __init__(
        self,
        mqtt_name: str,
        mqtt_topic: str,
        bus_id: str,
        offset: float,
        outlier_delta: float,
        outlier_window_size: int,
        noise_window_size: int,
    ):
        """Initialise sensor parameters and reading filters.

        Args:
            mqtt_name: MQTT sensor name, e.g. 'Living room temperature'
            mqtt_topic: MQTT topic, e.g. 'smart_thermostat'.
            bus_id: Sensor bus ID, e.g. '3ce1e3811e52'.
            offset: Positive or negative value to add to readings (°C).
            outlier_delta: Outlier filter parameter (see OutlierFilter docs).
            outlier_window_size: Outlier filter parameter (see OutlierFilter).
            noise_window_size: Noise filter parameter (see NoiseFilter docs).
        """
        self.mqtt = MQTTSensor.from_name(mqtt_topic, mqtt_name)
        self._ds18b20_id = bus_id
        self._ds18b20: AsyncW1ThermSensor | None = None
        self._offset = offset
        self._err_session: SensorErrorSession | None = None
        self._outlier_filter = OutlierFilter(
            sensor_id=self.mqtt.unique_id,
            outlier_delta=outlier_delta,
            window_size=outlier_window_size,
        )
        self._noise_filter = NoiseFilter(
            sensor_id=self.mqtt.unique_id,
            window_size=noise_window_size,
            window_amplitude=0.2,
            stability_delta=0.1,
        )

    @classmethod
    def from_config(
        cls, cfg: TemperatureSensorConfig, mqtt_topic: str
    ) -> TemperatureSensor:
        return cls(
            mqtt_topic=mqtt_topic,
            mqtt_name=cfg.mqtt_name,
            bus_id=cfg.bus_id,
            offset=cfg.offset,
            outlier_delta=cfg.outlier_delta,
            outlier_window_size=cfg.outlier_window_size,
            noise_window_size=cfg.noise_window_size,
        )

    @property
    def ds18b20(self) -> AsyncW1ThermSensor:
        if not self._ds18b20:
            self._ds18b20 = AsyncW1ThermSensor(
                sensor_type=W1Sensor.DS18B20, sensor_id=self._ds18b20_id
            )
        return self._ds18b20

    @property
    def error_session(self) -> SensorErrorSession:
        if not self._err_session:
            self._err_session = SensorErrorSession(self.mqtt.name)
        return self._err_session

    async def get_temperature(self) -> float:
        async def get_value_with_offset():
            return await self.ds18b20.get_temperature() + self._offset

        while True:
            try:
                # temperature = await self._filter_outlier(plus_offset)
                temperature = await self._outlier_filter.filter(
                    max_calls=3,
                    get_value=get_value_with_offset,
                )
            except (NoSensorFoundError, ResetValueError, SensorNotReadyError) as e:
                err_count = self.error_session.add_error(e)
                if err_count >= 3:
                    raise self.error_session.get_error(e)

                await asyncio.sleep(3)
                continue

            self._err_session = None
            return self._noise_filter.filter(temperature)
