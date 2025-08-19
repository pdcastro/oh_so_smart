# Build your own images, advanced debugging and contributing

- [Build your own images, advanced debugging and contributing](#build-your-own-images-advanced-debugging-and-contributing)
  - [Build your own images](#build-your-own-images)
    - [Workstation requirements](#workstation-requirements)
    - [Using the scripts](#using-the-scripts)
  - [Advanced debugging](#advanced-debugging)
    - [Manually starting the application](#manually-starting-the-application)
  - [Application architecture](#application-architecture)
    - [Application structure and product-specific code](#application-structure-and-product-specific-code)
    - [TOML configuration file format (schema)](#toml-configuration-file-format-schema)
    - [Design choices](#design-choices)
      - [Why TOML and not YAML or JSON?](#why-toml-and-not-yaml-or-json)
      - [Why use containers?](#why-use-containers)
      - [Why a Raspberry Pi? Doesn’t the Pico/ESPHome do the job?](#why-a-raspberry-pi-doesnt-the-picoesphome-do-the-job)
  - [Development tips](#development-tips)
    - [‘rsync’ to a running Oh So Smart container](#rsync-to-a-running-oh-so-smart-container)
    - [‘pip install gpiod’ on Windows or macOS](#pip-install-gpiod-on-windows-or-macos)
  - [Submitting a pull request](#submitting-a-pull-request)

## Build your own images

You may wish or need to build an Oh So Smart Docker image locally (instead of using the
images from the [GitHub Container
Registry](https://github.com/pdcastro/oh_so_smart/pkgs/container/oh_so_smart)), for
example in case you are making changes to the source code.

### Workstation requirements

***Workstation*** refers to your laptop or desktop computer (Mac, Windows or Linux), as
opposed to the [target device](/README.md#requirements) (e.g. a Raspberry Pi where Oh So
Smart runs).

An Oh So Smart Docker image can be built on either a workstation or the target device. A
recommended workflow is to build the image on the workstation and then transfer it over
the network to the target device, as this is usually faster. Python scripts are provided
to automate this task. The scripts run a few commands on the target device over ‘ssh’, for
example ‘docker load’ to transfer the Docker image. To this end, on your workstation:

* Install a recent version of [Python](https://www.python.org/downloads/) (3.12 or later).  
  Hint: Manage multiple versions of Python with [pyenv](https://github.com/pyenv/pyenv/)
  or [pyenv-win](https://github.com/pyenv-win/pyenv-win).
* Install Docker:
  - [Docker Engine](https://docs.docker.com/engine/install/) on a Linux workstation, or
  - [Docker Desktop](https://www.docker.com/products/docker-desktop/) on a Windows, macOS or
    Linux workstation.
* Configure SSH public key authentication to access the target device over the network,
  as follows.

GitHub has a good guide on [Generating a new SSH key and adding it to the
ssh-agent](https://docs.github.com/en/enterprise-cloud@latest/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent)
on all platforms (Windows, macOS, Linux). On Windows, Microsoft provides a guide as well:
[Key-based authentication in OpenSSH for
Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).
Don’t miss the part about configuring the ‘ssh-agent’ service and using ‘ssh-add’ to load
the keys in the agent.

After SSH public key authentication is setup as per guides, create or edit the file
‘~/.ssh/config’ (where ‘~’ is a placeholder for your user directory, e.g. ‘/Users/paulo’
on a macOS or Windows workstation) and add a ‘Host’ entry for your target device, for
example:

```sh
Host pi4
    Hostname 192.168.1.2
    User root
    Port 22
    PreferredAuthentications publickey
    ConnectTimeout 5
```

Note that there are two different “host names” in the example above:

* ‘pi4’ is the name of the ssh config ‘Host’ entry. Choose any name you like, but it must
  match the ‘ssh_host_name’ setting in the Oh So Smart [TOML configuration
  file](#configuring-oh-so-smart). This name does ***not*** need to match any networking
  configuration of the target device (in particular, the device’s network hostname).

* ‘192.168.1.2’ is the target device’s IP address or valid network hostname. It can also
  be an mDNS hostname such as ‘pi4.local’ if the target device’s operating system is the
  Raspberry Pi OS or balenaOS and the target device’s hostname was configured accordingly.

This setup substantially simplifies both manually typed and scripted ssh command lines.
For example, the ‘ssh pi4’ command opens a shell on the target device without requiring
the specification of IP address, hostname, port number, username, password... on each
command line.

<details>
<summary>Click to expand: Check that your SSH public key authentication config is working</summary>

On the workstation, check that the following commands succeed:

```sh
# Load your private key in the ssh-agent
ssh-add
# List the loaded keys
ssh-add -l

# Run a remote command. Replace ‘pi4’ with the name of the ‘Host’
# entry in the ‘~/.ssh/config’ file. This command should succeed
# without prompting you to type a password. Sample expected output:
#     > ssh pi4 grep ID= /etc/os-release
#     ID="balena-os"
#     VERSION_ID="5.1.20"
ssh pi4 grep ID= /etc/os-release
```
</details>

### Using the scripts

In the following steps, replace ‘config.toml’ with the path to your actual configuration
file.

_On a workstation shell prompt (e.g. bash or PowerShell):_

```sh
# Check that your SSH public key authentication config is working.
# This command should succeed without prompting you to type a password.
# Replace ‘pi4’ with the name of the Host section in your ~/.ssh/config
# file for your target device. (See Workstation Requirements docs.)
ssh pi4 grep ID= /etc/os-release

# Clone or download the git repository.
git clone https://github.com/pdcastro/oh-so-smart

# ‘cd’ to the folder where the repo was cloned or extracted.
cd oh-so-smart

# Build the Oh So Smart Docker image.
python scripts/docker.py -c config.toml build

# Transfer the Docker image from the workstation to the target device.
python scripts/docker.py -c config.toml save

# Transfer the config file and a few script files to the target device.
python scripts/upload.py -c config.toml
```

_Open an ssh shell to the target device (e.g. a Raspberry Pi) and start the app:_

```sh
# Open a ssh shell on the target device.
# Replace ‘pi4’ with the name of the Host section in your ~/.ssh/config
# file for your target device. (See Workstation Requirements docs.)
ssh pi4

# ‘cd’ to the folder where files were uploaded, e.g. with ‘scripts/upload.py’.
cd host_os_project_dir  # Folder defined in ‘config.toml’

# Run the Oh So Smart container on the target device. Performance hint: Replace
# ‘docker.sh’ with ‘docker.py’ if the host OS has a ‘python3’ interpreter.
./scripts/docker.sh -c config.toml run

# Check the Docker container logs. Replace ‘smart_thermostat’ with the
# actual container name, which is the ‘slug’ setting in ‘config.toml’.
# Replace ‘docker’ with ‘podman’ on Fedora IoT or ‘balena-engine’ on balenaOS.
docker logs -f smart_thermostat
```

## Advanced debugging

### Manually starting the application

For development or advanced debugging, it may be useful to create an Oh So Smart container
that lands on a shell prompt, so you can manually start and stop the application:

```sh
# Open a ssh shell on the target device.
# Replace ‘pi4’ with the name of the Host section in your ~/.ssh/config
# file for your target device. (See Workstation Requirements docs.)
ssh pi4

# ‘cd’ to the folder where files were uploaded, e.g. with ‘scripts/upload.py’.
cd host_os_project_dir  # Folder defined in ‘config.toml’

# Create the Oh So Smart app container and open a shell on it, but do not
# start the app yet. Note how the command line ends with ‘sh’: The ‘sh’
# command will be executed instead of the Oh So Smart app. Performance hint:
# Replace ‘docker.sh’ with ‘docker.py’ if the host OS has Python installed.
./scripts/docker.sh -c config.toml run sh

# Now, on the shell prompt inside the container, manually start the
# Oh So Smart app in debug mode and watch the logs in real time. The TOML
# config file will be available at ‘/data’ through a Docker bind mount.
cd /oh_so_smart
DEBUG=1 ./env/bin/python -m oh_so_smart -c /data/config.toml

# Stop the app by hitting CTRL-C.
CTRL-C
```

## Application architecture

The Oh So Smart application continuously listens for MQTT command messages from Home
Assistant and sends back switch state updates and sensor readings. The application is
IO-bound, with light CPU usage. It was implemented with Python’s
[asyncio](https://docs.python.org/3/library/asyncio.html) library for cooperative
concurrency. The [paho-mqtt](https://pypi.org/project/paho-mqtt/) and the
[gpiod](https://pypi.org/project/gpiod/) libraries are used for MQTT communication and
GPIO pin access respectively.

Error handling is extensive but in the event of a fatal condition that causes the
application to exit, a separate “launcher” process
([restarter.py](oh_so_smart/restarter.py)) takes care of restarting the application
_within the Docker container,_ with a delay between restarts to avoid a busy loop. If
something more fundamental fails and the container exits, the Docker Engine restarts the
container thanks to the ‘docker run --restart=unless-stopped’ restart policy.

### Application structure and product-specific code

The application is structured around the following core parts:

* Two producer/consumer
  [asyncio.Queue](https://docs.python.org/3/library/asyncio-queue.html) queues
  ([queue.py](oh_so_smart/mqtt/queue.py)), being one “receive queue” and one “send queue”
  that store incoming and outgoing MQTT messages respectively.
* An MQTT Manager ‘asyncio’ Task ([mqtt/manager.py](oh_so_smart/mqtt/manager.py)) that:
  - Receives incoming messages from the MQTT broker and puts them in the receive queue.
  - Takes outgoing messages from the send queue and sends them to the MQTT Broker.
* A Switch Manager ‘asyncio’ Task ([switches/manager.py](oh_so_smart/switches/manager.py))
  that:
  - Consumes command messages from the receive queue (e.g. turn on/off switches) and
    acts on the GPIO pins accordingly.
  - Produces on/off switch status messages and add them to the send queue.
* A Sensor Manager ‘asyncio’ Task ([sensors/manager.py](oh_so_smart/sensors/manager.py))
  that regularly takes sensor (temperature) readings and puts them in the send queue.

These core parts are shared among all smart device “products,” like a Smart Plug/Socket or
Smart Thermostat. Product-specific customizations go in the
[products](/oh_so_smart/products/) folder. See examples in ‘products/smart_socket/’ and
‘products/smart_thermostat/’.

### TOML configuration file format (schema)</summary>

Thanks to [Pydantic](https://pypi.org/project/pydantic/), the TOML configuration file
format (schema) is specified by the source code data structures themselves, in
[schema.py](oh_so_smart/config/schema.py). This allows for a “single source of truth,”
avoiding conflicts between the source code and an external formal specification.

### Design choices 

#### Why TOML and not YAML or JSON?

Standard JSON does not allow comments around data structures. Comments allow documentation
to be provided within sample configuration files, where it is most helpful. Between TOML
and YAML, TOML was chosen because it has built-in support in the Python standard library,
while YAML would require a third-party package to be installed _in order to run the
auxiliary Python scripts_ in the [scripts](./scripts/) folder. Installing dependencies is
not a big deal for the core Oh So Smart application that runs in a Docker container,
however for the auxiliary Python scripts, it would mean adding a stumbling block for end
users. As it stands, the auxiliary Python scripts do not require any third-party Python
packages to be installed.

#### Why use containers?

Running the Oh So Smart application in a Docker container is recommended for a few
reasons:

* It allows you to take advantage of the Docker Engine’s container restart policy ([docker
  run --restart
  unless-stopped](https://docs.docker.com/reference/cli/docker/container/run/#restart))
  that automatically restarts the application when the device reboots. The ‘run’
  subcommand of the [docker.py](./scripts/docker.py) deployment script does this by
  default. This saves you the trouble of configuring ‘systemd’ services or building
  custom OS images of immutable Linux distros, e.g. Ubuntu Core or Fedora IoT.
* The Docker image used to run the containers is automatically built with all
  dependencies, including a suitable installation of the Python interpreter and the
  ‘libgpiod’ package.
* If you chose to modify the source code, containers allows you to test your changes to
  some extent on your workstation, before deploying to the target device.
* If you needed to report bugs, containers make it easier to setup a reproducible
  environment.

#### Why a Raspberry Pi? Doesn’t the Pico/ESPHome do the job?

[68 million Raspberry Pis have been sold as of March
2025](https://en.wikipedia.org/wiki/Raspberry_Pi)! For those who happen to have a
Raspberry Pi idly lying around, Oh So Smart is a way of putting it to good use. But indeed
a Raspberry Pi running an Oh So Smart container and nothing else will have the CPU 99%
idle. The same Pi could be used for other purposes though, like running Home Assistant,
Mosquitto or other projects — especially with the use of Docker containers.


## Development tips

### ‘rsync’ to a running Oh So Smart container

A possible development workflow is to edit the source code on your workstation and
iteratively ‘rsync’ your changes directly to a running Oh So Smart container on the target
device. You can then manually start the app in debug mode as described above.

The [upload.py](scripts/upload.py) script makes this workflow easier by automatically
filling in the tricky ‘--rsh’ option of ‘rsync’. It requires ‘rsync’ to be installed on
the workstation. On a Windows workstation, this probably means using the Windows Subsystem
for Linux. ‘rsync’ does _not_ need to be installed on the target device host OS, and it is
already included on the Oh So Smart Docker image/container.

_To upload the project files from the workstation to the target device:_

```sh
# On your workstation, ‘cd’ to the folder where the repo was cloned or
# extracted (where you are editing the source code).
cd oh-so-smart

# Create the Oh So Smart container on the target device as documented above.
# Then, on the workstation, run the ‘upload.py’ script with the ‘--container’
# option:
python scripts/upload.py -c config.toml --container
```

### ‘pip install gpiod’ on Windows or macOS

When developing on a workstation with an IDE such as VS Code, it is useful to have
“IntelliSense-like” code completion and error highlighting for all parts of the code,
including usage of the [gpiod](https://pypi.org/project/gpiod/) library. Sadly, ‘pip
install gpiod’ fails on Windows and macOS when pip tries to build a wheel, because ‘gpiod’
depends on the Linux kernel. To work around this issue, run the
[scripts/pip_install.py](scripts/pip_install.py) script on the workstation. It creates a
temporary Docker container from a [Python image](https://hub.docker.com/_/python),
installs ‘gpiod’ in the container (which succeeds, as the Docker VM has a Linux kernel),
and then copies the ‘site-packages/gpiod’ folder back to the workstation, minus the
incompatible binary wheel.


## Submitting a pull request

Before submitting a PR, please run the [scripts/lint.py](scripts/lint.py) script and fix
any errors or warnings. It runs [pylint](https://pypi.org/project/pylint/),
[pyright](https://github.com/microsoft/pyright) and
[ruff](https://github.com/astral-sh/ruff) (‘ruff check’ and ‘ruff format’) on Python code,
and [shellcheck](https://github.com/koalaman/shellcheck/) on shell scripts.

Also, please follow the [conventional commits](https://www.conventionalcommits.org/)
convention for commit messages, because the [Python Semantic
Release](https://python-semantic-release.readthedocs.io/en/stable/) tool is used in order
to:

* Automatically compute the next [semver](https://semver.org)-compliant release version
  based on commit message prefixes. The current configuration in
  [pyproject.toml](/pyproject.toml) maps the ‘feat’ prefix to a minor version bump and the
  ‘fix’, ‘perf’ and ‘chore’ prefixes to a patch version bump.
* Automatically update the [CHANGELOG.md](/CHANGELOG.md) file.
* Automatically generate a GitHub release with a description based on a [custom
  template](/docs/psr-templates/.release_notes.md.j2) that includes the Docker image tags
  associated with the release.

