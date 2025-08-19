"""Factory functions for the Smart Socket product.

Factory functions are used to make product-specific objects of common base classes.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from ...config.schema import CoreConfig, validate_config
from ..factory import (
    CoreObjects,
    ProductObjects,
    make_switch_groups,
)
from ..gpio import setup_gpio_out_pins


class SmartSocketConfig(CoreConfig):
    """Optional specialisation of the configuration file schema."""


def make_objects(core: CoreObjects) -> ProductObjects:
    # pylint: disable=R0801
    # R0801: Similar lines in 2 files
    cfg = validate_config(core.pre_config.config_dict, SmartSocketConfig)
    gpio_line = setup_gpio_out_pins(cfg)
    switch_groups = make_switch_groups(cfg.switch_groups, gpio_line)
    return ProductObjects(config=cfg, switch_groups=switch_groups)
