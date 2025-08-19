"""MQTT message data structures.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from dataclasses import dataclass
from enum import auto, Enum
from typing import Any

from paho.mqtt.properties import Properties


class MsgType(Enum):
    ON_CONNECT = auto()
    ON_EXIT = auto()
    MQTT_WILL = auto()
    SUBSCRIBE = auto()


@dataclass
class Msg:
    topic: str
    payload: str | None = None
    qos: int = 2
    retain: bool = True
    properties: Properties | None = None
    type: MsgType | None = None

    def mqtt_args(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "payload": self.payload,
            "qos": self.qos,
            "retain": self.retain,
            "properties": self.properties,
        }
