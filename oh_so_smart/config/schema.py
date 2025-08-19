"""Main configuration model classes (configuration file schema).

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

import os
from dataclasses import dataclass
from typing import Annotated, Any, Optional, get_type_hints, override

from pydantic import BaseModel, Field, ValidationError

from .gpio import GPIOConfig, GPIOPinConfig, W1GPIOConfig


class ConfigError(Exception):
    pass


class MQTT_GPIO_Config(GPIOPinConfig):  # pylint: disable=invalid-name
    mqtt_name: str
    slug: str = ""


BinarySensorConfig = MQTT_GPIO_Config
SwitchConfig = MQTT_GPIO_Config


class SwitchGroupConfig(BaseModel):
    mqtt_topic: str
    switches: list[SwitchConfig]
    keep_alive_timeout_sec: int = 0


class BinarySensorGroupConfig(BaseModel):
    mqtt_topic: str
    sensors: list[BinarySensorConfig]


class TemperatureSensorConfig(BaseModel):
    mqtt_name: str
    type: str  # Example: 'ds18b20'
    bus_id: str  # Example: '3ce1e3811e52' (DS18B20 sensor ID)
    offset: float = 0.0  # Positive or negative value added to all measurements
    outlier_delta: float = 6.0
    outlier_window_size: int = 3
    noise_window_size: int = 3


class TemperatureSensorGroupConfig(W1GPIOConfig):
    mqtt_topic: str
    poll_interval_sec: int
    tolerate_missing_sensors: bool
    sensors: list[TemperatureSensorConfig]


class MQTTServer(BaseModel):
    hostname: str = ""
    username: str = ""
    password: str = ""
    port: int = 0


class MQTTConfig(BaseModel):
    client_id: str
    server: MQTTServer = MQTTServer()

    # pylint incorrectly reports that the parent has a different number of arguments.
    # pylint: disable=arguments-differ
    @override
    def model_post_init(self, context: Any):
        """Use values from environment variables (if any) for the MQTTServer fields."""

        for field_name in ("hostname", "port", "username", "password"):
            self._validate_mqtt_server_field(field_name)

        return super().model_post_init(context)

    def _validate_mqtt_server_field(self, name: str):
        env_name = f"MQTT_SERVER_{name.upper()}"
        env_str = os.environ.get(env_name, "").strip()
        # An env var (if set) takes precedence over the config file setting.
        if env_str:
            field_type: type[str | int] = get_type_hints(MQTTServer)[name]
            try:
                env_value = field_type(env_str)
            except ValueError as e:
                raise ValueError(
                    f"Error validating environment variable ‘{env_name}’: "
                    f"value ‘{env_str}’ cannot be parsed as type ‘{field_type.__name__}’"
                ) from e
            setattr(self.server, name, env_value)

        if not getattr(self.server, name):
            # MQTT_SERVER_PORT -> mqtt.server.port
            cfg_name = env_name.replace("_", ".").lower()
            raise ValueError(
                f"Missing or invalid ‘{cfg_name}’ configuration or ‘{env_name}’ environment variable"
            )


class DeploymentConfig(BaseModel):
    # docker_platforms: List of Docker platform identifiers for the target
    # device (the device that runs the Oh So Smart app, e.g. a Raspberry Pi).
    # Used by deployment scripts. Example: ['arm/v7', 'arm64', 'amd64']
    docker_platforms: list[str]
    # python_distro: The Python Docker image variant, either 'alpine' or
    # 'debian'. Used by deployment scripts.
    python_distro: Annotated[str, Field(pattern=r"^(alpine|debian)$")]
    # ssh_host_name: The name of the ‘Host’ entry in the workstation’s
    # ‘~/.ssh/config’ file that configures ssh access to the target device
    # (e.g. Raspberry Pi). Used by deployment scripts.
    ssh_host_name: str
    # Project directory in the target device’s host OS filesystem where the
    # Oh So Smart application files are deployed. Used by deployment scripts.
    host_os_project_dir: str


class ProductConfig(BaseModel):
    # slug: Product short name such as "smart_thermostat" or "smart_socket".
    slug: str
    # source_dir: Subdirectory of oh_so_smart/products that contains
    # product-specific source code.
    source_dir: str


class CoreConfig(BaseModel):
    product: ProductConfig
    deployment: Optional[DeploymentConfig] = None
    mqtt: Optional[MQTTConfig] = None
    # pydantic's BaseModel correctly handles mutable default values (= [])
    switch_groups: list[SwitchGroupConfig] = []
    temperature_sensor_groups: list[TemperatureSensorGroupConfig] = []
    binary_sensor_groups: list[BinarySensorGroupConfig] = []
    gpio: list[GPIOConfig] = []


@dataclass
class PreValidationConfig:
    config_dict: dict[str, Any]
    product_source_dir: str


def load_config_file(config_file: str) -> PreValidationConfig:
    """Load and parse the TOML configuration file."""
    from tomllib import load, TOMLDecodeError

    with open(config_file, "rb") as f:
        try:
            config_obj = load(f)
        except TOMLDecodeError as e:
            raise ConfigError(
                f"Failed to parse configuration file '{config_file}':\n{e}"
            ) from e
        try:
            source_dir = config_obj["product"]["source_dir"]
            if not source_dir or not isinstance(source_dir, str):
                raise ValueError("Empty string")
        except (KeyError, ValueError) as e:
            raise ConfigError(
                f"Missing 'product.source_dir' value in configuration file '{config_file}'"
            ) from e

        return PreValidationConfig(config_obj, source_dir)


def validate_config[T: CoreConfig](config_obj: dict, config_type: type[T]) -> T:
    """Validate the TOML configuration file using a Pydantic model."""
    try:
        return config_type(**config_obj)
    except ValidationError as e:
        raise ConfigError(f"Invalid configuration file:\n{e}") from e
