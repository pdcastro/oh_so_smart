"""Pin number mapping between the Raspberry Pi board/header pin numbering and
the Broadcom/BCM GPIO pin numbering used by libraries like `libgpiod`.

Reminder of the default state of the built-in, configurable pull-up/down
resistors when the Pi boots:
- GPIO pins 0-8 are input, pulled up by default.
- GPIO pins 9-27 are input, pulled down by default.
The above are logical (BCM chip) GPIO pin numbers, not board header pin numbers.

References:
- https://datasheets.raspberrypi.com/bcm2835/bcm2835-peripherals.pdf
- https://datasheets.raspberrypi.com/bcm2711/bcm2711-peripherals.pdf
- https://raspberrypi.stackexchange.com/questions/113571/which-rpi-pins-are-pulled-up-or-down-during-startup
- https://www.google.com/search?q=raspberry+pi+pin+numbers

---
Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from typing import Final

# fmt: off
BOARD_TO_CHIP: Final = (0, 0, 0, 2, 0, 3, 0, 4, 14, 0, 15, 17, 18, 27, 0, 22, 23, 0, 24, 10, 0, 9, 25, 11, 8, 0, 7, 0, 0, 5, 0, 6, 12, 13, 0, 19, 16, 26, 20, 0, 21)
CHIP_TO_BOARD: Final = (0, 0, 3, 5, 7, 29, 31, 26, 24, 21, 19, 23, 32, 33, 8, 10, 36, 11, 12, 35, 38, 40, 15, 16, 18, 22, 37, 13)
# fmt: on


def board_to_chip(header_pin: int) -> int:
    return BOARD_TO_CHIP[header_pin]


def chip_to_board(chip_pin: int) -> int:
    return CHIP_TO_BOARD[chip_pin]


def _make_board_to_chip_map() -> list[int]:
    # fmt: off
    # board_odd = (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39)
    odd_to_chip = (0, 2, 3, 4, 0, 17, 27, 22,  0, 10,  9, 11,  0,  0,  5,  6, 13, 19, 26,  0)

    # board_even = (0, 2, 4, 6,  8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40)
    even_to_chip = (0, 0, 0, 0, 14, 15, 18,  0, 23, 24,  0, 25,  8,  7,  0,  0, 12,  0, 16, 20, 21)
    # fmt: on

    return [(even_to_chip, odd_to_chip)[i % 2][i // 2] for i in range(41)]


def _print_pin_maps():
    btc_map = _make_board_to_chip_map()
    print(f"BOARD_TO_CHIP={btc_map} (len={len(btc_map)})")

    ctb_map = [0] * (max(btc_map) + 1)
    for board_pin, chip_pin in enumerate(btc_map):
        ctb_map[chip_pin] = board_pin

    ctb_map[0] = 0  # pin value '0' is used to refer to non-GPIO pins like 3V3

    print(f"CHIP_TO_BOARD={ctb_map} (len={len(ctb_map)})")


if __name__ == "__main__":
    _print_pin_maps()
