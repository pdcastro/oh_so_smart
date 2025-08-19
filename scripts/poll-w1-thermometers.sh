#!/usr/bin/env bash

# This script polls DS18B20 thermometers connected to a Linux device such as
# a Raspberry Pi. It should be run on the device itself. For DS18B20 sensors to
# work on a Raspberry Pi, don’t forget to add a line with ‘dtoverlay=w1-gpio’
# to the Pi’s ‘config.txt’, then rebooting.
#
# Copyright (C) 2025 Paulo Ferreira de Castro
#
# Licensed under the Open Software License version 3.0, a copy of which can be
# found in the LICENSE file.


# Repeatedly parse the contents of input files such as:
#   ‘/sys/bus/w1/devices/28-0722526be734/w1_slave’
#   ‘/sys/bus/w1/devices/28-3ce1e3811e52/w1_slave’
#   ...
# and print output lines such as:
#   0722526be734 20.000°C 68.000°F   3ce1e3811e52 20.375°C 68.675°F
#   0722526be734 20.250°C 68.450°F   3ce1e3811e52 20.500°C 68.900°F
#   ...
poll_devices() {
	local file celsius fahrenheit f10k id milliC
	while :
	do
		for file in /sys/bus/w1/devices/28-*/w1_slave
		do
			if [[ "$(< "${file}")" =~ ' t='([0-9]+) ]]
			then
				milliC="${BASH_REMATCH[1]}" # E.g. ‘20250’ (millicelsius)
				celsius="${milliC:0: -3}.${milliC: -3:3}"
				# f10k = 10,000 * fahrenheit = 10,000 * (milliC/1000 * 9/5 + 32)
				f10k=$((milliC * 18 + 320000))
				fahrenheit="${f10k:0: -4}.${f10k: -4:3}"
				[[ "${file}" =~ /28-([^/]+)/ ]] && id="${BASH_REMATCH[1]}" || id=\?
				echo -n "${id} ${celsius}°C ${fahrenheit}°F   "
			else
				echo "Error parsing ‘${file}’"
			fi
		done
		echo
		sleep 1
	done
}

poll_devices
