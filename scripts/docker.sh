#!/usr/bin/env bash
# shellcheck shell=bash

# This bash script executes the ‘docker.py’ Python script in a Docker container
# with the ‘--print-command-lines’ flag, and executes the printed lines in the
# environment of the bash script. This allows ‘docker.py’ to be executed on a
# immutable, minimal host OS like Ubuntu Core or balenaOS, with a read-only root
# filesystem that does not include a Python interpreter.
#
# Copyright (C) 2025 Paulo Ferreira de Castro
#
# Licensed under the Open Software License version 3.0, a copy of which can be
# found in the LICENSE file.

set -e

quit() {
	echo -e "\n${1}"
	exit 1
}

# Directory where this script is located. This is also assumed to be
# location of the docker.py script.
PROJECT_DIR="$(cd -- "$(dirname "$0")/.." >/dev/null 2>&1 || true; pwd -P)"
LOCAL_IMAGE_NAME='oh_so_smart:latest'
# Note that ‘container’ here refers to a temporary Docker container created
# by this shell script in order to run the ‘docker.py’ Python script.
CONTAINER_CONFIG_DIR='/data'
CONTAINER_PROJECT_DIR='/oh_so_smart'
CONTAINER_SCRIPT_DIR='/oh_so_smart/scripts'
DOCKER="$(command -v docker || command -v podman || command -v balena-engine || echo docker)"


# Return 0 (success) if $1 starts with $2, otherwise return 1 (failure). E.g.:
#   starts_with foobar foo -> 0 (success)
#   starts_with foobar goo -> 1 (failure)
starts_with() {
	[ "${1:0:${#2}}" = "$2" ]
}

# Return 0 (success) if $1 ends with $2, otherwise return 1 (failure). E.g.:
#   ends_with foobar bar -> 0 (success)
#   ends_with foobar boo -> 1 (failure)
ends_with() {
	[ "${1: -${#2}}" = "$2" ]
}

# Return 0 (success) if ‘podman’ is being used, otherwise return 1 (failure).
is_podman() {
	ends_with "${DOCKER}" podman
}

# Set a HOST_OS_DATA global array with the data expected by the ‘--host-os-data’
# option of the ‘docker.py’ script.
gather_host_os_data() {
	declare -ag HOST_OS_DATA=(project_dir "${PROJECT_DIR}")

	if [ -n "${CONFIG_FILE}" ]
	then
		HOST_OS_DATA+=(config_file "${CONFIG_FILE}")
	fi

	local img_ref=
	is_podman && img_ref="localhost/${LOCAL_IMAGE_NAME}" || img_ref="${LOCAL_IMAGE_NAME}"
	if [ -n "$("${DOCKER}" images --format '{{.Repository}}:{{.Tag}}' "${img_ref}")" ]
	then
		HOST_OS_DATA+=(image_for_run_command "${LOCAL_IMAGE_NAME}")
	fi

	if [ -n "${USER}" ]
	then
		HOST_OS_DATA+=(user "${USER}")
	fi
	if command -v uname >/dev/null
	then
		HOST_OS_DATA+=(uname_o "$(uname -o)")
	fi
	# Extract the ID field of ‘/etc/os-release’, e.g. ‘ubuntu-core’
	if [ -r /etc/os-release ] && [[ "$(< /etc/os-release)" =~ (^|$'\n')ID=([^$'\r\n']+) ]]
	then
		HOST_OS_DATA+=(os_release_id "${BASH_REMATCH[2]}")
	fi
	# Extract a comma-separated user list for the ‘docker’ group, e.g. ‘user1,user2’
	if [ -r /etc/group ] && [[ "$(< /etc/group)" =~ (^|$'\n')docker:[^:]*:[^:]*:([^:$'\r\n']+) ]]
	then
		HOST_OS_DATA+=(docker_group_users "${BASH_REMATCH[2]}")
	fi
}

# Parse a Docker command line given as argument in order to extract a container
# name (if any) from the ‘docker run --name’ option. If found, print a message.
print_container_name() {
	local cmd="$1"
	if starts_with "${cmd}" 'run ' && \
		# If it is not an interactive container
		[[ "${cmd}" != *' --interactive '* && "${cmd}" != *' -i '* ]] && \
		# https://github.com/moby/moby/blob/c3a7df35e7c024dca6fc43bf02dd30783e912825/daemon/names/names.go#L6
		[[ "${cmd}" =~ --name(=|[$'\t'\ ]+)([a-zA-Z0-9][a-zA-Z0-9_.-]*)([$'\t'\ ]|$) ]]
	then
		local name="${BASH_REMATCH[2]}"
		echo "Container ‘${name}’ started. Check the logs with:
${DOCKER@Q} logs -f ${name}"
	fi
}

# Select a Python image for running the ‘docker.py’ script, giving preference to
# suitable images that are already available locally to save time. In principle
# any image with a ‘/usr/local/bin/python’ v12+ interpreter would do.
get_python_image() {
	local default='python:3.13-alpine3.22' # Same as used in ‘Dockerfile.alpine’
	local nametag="${default}"
	if is_podman
	then
		# ‘podman’ lists Docker Hub images with a ‘docker.io/library/’ prefix
		# and local images with a ‘localhost/’ prefix.
		local candidates=(
			"docker.io/library/${default}"
			'localhost/oh_so_smart:latest' 'localhost/oh_so_smart'
		)
	else
		local candidates=("${default}" 'oh_so_smart:latest' 'oh_so_smart')
	fi
	candidates+=('ghcr.io/pdcastro/oh_so_smart:latest' 'ghcr.io/pdcastro/oh_so_smart')

	local -a images=()
	readarray -t images < <(\
		"${DOCKER}" images --format '{{.Repository}}:{{.Tag}}' | grep -v '<none>')

	for candidate in "${candidates[@]}"
	do
		for image in "${images[@]}"
		do
			image="${image%%$'\r'}"
			if [ "${image}" = "${candidate}" ] || starts_with "${image}" "${candidate}:"
			then
				nametag="${image}"
				break 2
			fi
		done
	done
	echo "${nametag}"
}

# Create and run a Docker container from a stock Docker Hub Python image in
# order to execute the ‘docker.py --print-command-lines’ script that prints
# command lines to stdout, then execute the printed command lines in this
# shell script’s “host OS” context. This is useful when it is not possible
# or desirable to install a Python interpreter on the host OS, for example
# on an immutable, container-first host OS like Ubuntu Core or balenaOS.
run_docker() {
	local -a config_opts=()
	local -a bind_mounts=("${PROJECT_DIR}:${CONTAINER_PROJECT_DIR}")

	if [ -n "${CONFIG_FILE}" ]
	then
		local container_config_file="${CONTAINER_CONFIG_DIR}/${CONFIG_BASENAME}"
		bind_mounts+=("${CONFIG_FILE}:${container_config_file}")
		config_opts+=(--config-file "${container_config_file}")
	fi

	gather_host_os_data

	shopt -s nullglob
	local -a gpio_devs=(/dev/gpiochip*)
	local cmd=(\
		"${DOCKER}" run --name docker.py --rm --env DEBUG \
		--workdir "${CONTAINER_PROJECT_DIR}" \
		--security-opt label=disable \
		"${bind_mounts[@]/#/--volume=}" \
		"${gpio_devs[@]/#/--device=}" \
		--entrypoint /usr/local/bin/python \
		"$(get_python_image)" \
		"${CONTAINER_SCRIPT_DIR}/docker.py" \
		"${config_opts[@]}" \
		--project-dir "${CONTAINER_PROJECT_DIR}" \
		--host-os-data "${HOST_OS_DATA[@]}" \
		--print-command-lines "${PARSED_OPTS[@]}" \
	)
	echo + "${cmd[@]}"
	# Note: Instead of ‘readarray’, an earlier solution used the construct:
	#     "${cmd[@]}" | while IFS= read -r line; do ... ; done
	# The problem with that solution is that the body of the ‘while’ loop
	# executes in a subshell whose standard input is the pipe, not a TTY
	# terminal. As a result, a ‘docker run -i’ command executed in the body
	# of the loop would produce the error “the input device is not a TTY”.
	local line
	local -a cmd_lines=()
	readarray -t cmd_lines < <("${cmd[@]}")
	for line in "${cmd_lines[@]}"
	do
		# Commands lines printed with ‘docker.py -p’ start with ‘+## ’.
		# If found, remove the prefix and execute the command line.
		local prefix='+## docker '
		if starts_with "${line}" "${prefix}"
		then
			local subcmd="${line#"${prefix}"}"
			subcmd="${subcmd%%$'\r'}"
			local eval_str="${DOCKER@Q} ${subcmd}"
			echo -e "\r\n+ ${eval_str}\r"
			if eval "${eval_str}"
			then
				print_container_name "${subcmd}"
			else
				# Quit on error, unless the command was ‘docker kill’ or ‘docker rm’.
				if	! starts_with "${subcmd}" 'rm ' && \
					! starts_with "${subcmd}" 'kill '
				then
					quit "Error executing docker command line. Aborting."
				fi
			fi
			sleep 0.1
		else
			echo "${line}"
		fi
	done
}

parse_opts() {
	declare -ga PARSED_OPTS=()
	declare -g CONFIG_FILE=
	declare -g CONFIG_BASENAME=
	command -v basename >/dev/null || quit "Error: ‘basename’ command not found"
	command -v dirname >/dev/null  || quit "Error: ‘dirname’ command not found"
	command -v realpath >/dev/null || quit "Error: ‘realpath’ command not found"
	while [[ $# -gt 0 ]]; do
		case "$1" in
			-c|--config-file)
				[ -f "$2" ] || quit "Config file not found: '$2'"
				CONFIG_FILE="$(realpath "$2")"
				CONFIG_BASENAME="$(basename "${CONFIG_FILE}")"
				shift
				shift
				;;
			*) PARSED_OPTS+=("$1"); shift;;
		esac
	done
}

parse_opts "$@" || quit 'Error parsing command-line options'
run_docker "$@"
