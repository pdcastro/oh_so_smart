"""Home Assistant MQTT entity naming conventions.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from dataclasses import dataclass


@dataclass
class HA_MQTT_Entity_Strings:  # pylint: disable=invalid-name
    unique_id: str  # E.g. 'smart_thermostat_living_room_temperature'
    name: str  # E.g. 'living_room_temperature'
    state_topic: str  # E.g. 'smart_thermostat/living_room_temperature'


def to_slug(name: str) -> str:
    """Convert a name like 'Smart Switch' to a slug identifier like 'smart_switch'."""
    return name.strip().lower().replace(" ", "_")


def get_ha_mqtt_entity_strings(
    mqtt_topic: str, mqtt_name: str
) -> HA_MQTT_Entity_Strings:
    """Format Home Assistant unique ID and state topic for sensors, switches, etc."""
    topic_slug = to_slug(mqtt_topic)
    name_slug = to_slug(mqtt_name)
    unique_id = f"{topic_slug}_{name_slug}"
    state_topic = f"{topic_slug}/{name_slug}"
    name = unique_id.replace("_", " ").title()
    return HA_MQTT_Entity_Strings(
        unique_id=unique_id, name=name, state_topic=state_topic
    )
